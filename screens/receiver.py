#!/usr/bin/python3

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DATA_DIR = os.environ.get("DE_SCREEN_DATA", "/data/screens")
SECRET_FILE = os.environ.get("DE_SCREEN_SECRET", "/etc/digital-examen/proxy.secret")
KEEP_DAYS = int(os.environ.get("DE_SCREEN_KEEP_DAYS", "10"))
PORT = int(os.environ.get("DE_SCREEN_PORT", "9840"))

MODES = {}
QUEUES = {}
RESULTS = {}
COUNTER = {"value": 0}


def read_secret():
    if not os.path.exists(SECRET_FILE):
        return ""
    return open(SECRET_FILE).read().strip()


def safe_login(login):
    out = ""
    for char in login.lower():
        if char.isalnum() or char in "_-":
            out += char
    return out


def frame_dir(login, stamp, frame_no):
    day = time.strftime("%Y-%m-%d", time.localtime(stamp))
    return os.path.join(DATA_DIR, day, login, "%010d" % frame_no)


def store(header, blobs):
    login = safe_login(header["login"])
    path = frame_dir(login, header["time"], header["frame"])
    os.makedirs(path, exist_ok=True)

    names = []
    for position, piece in enumerate(header["pieces"]):
        index = piece[0]
        name = "full.webp" if index < 0 else "t%d.webp" % index
        out = open(os.path.join(path, name), "wb")
        out.write(blobs[position])
        out.close()
        names.append(name)

    meta = {
        "login": login,
        "frame": header["frame"],
        "time": header["time"],
        "size": header["size"],
        "grid": header["grid"],
        "tile": header["tile"],
        "keyframe": header["keyframe"],
        "pieces": [piece[0] for piece in header["pieces"]],
        "files": names,
    }
    out = open(os.path.join(path, "meta.json"), "w")
    out.write(json.dumps(meta))
    out.close()

    latest = open(os.path.join(DATA_DIR, "latest-" + login + ".json"), "w")
    latest.write(json.dumps(meta))
    latest.close()

    if header.get("thumb"):
        thumb = open(os.path.join(DATA_DIR, "thumb-" + login + ".webp"), "wb")
        thumb.write(blobs[-1])
        thumb.close()


class Handler(BaseHTTPRequestHandler):

    def log_message(self, *args):
        return

    def reply(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        route = self.path.rstrip("/")

        if route == "/result":
            secret = read_secret()
            if not secret or self.headers.get("X-De-Secret") != secret:
                self.reply(403, {"error": "bad secret"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1024 * 1024:
                self.reply(400, {"error": "bad length"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except ValueError:
                self.reply(400, {"error": "bad json"})
                return
            batch = str(payload.get("id", "")).split(":")[0]
            RESULTS.setdefault(batch, []).append(payload)
            self.reply(200, {"ok": True})
            return

        if route != "/frame":
            self.reply(404, {"error": "unknown path"})
            return

        secret = read_secret()
        if not secret or self.headers.get("X-De-Secret") != secret:
            self.reply(403, {"error": "bad secret"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 32 * 1024 * 1024:
            self.reply(400, {"error": "bad length"})
            return

        body = self.rfile.read(length)
        split = body.find(b"\n")
        if split < 0:
            self.reply(400, {"error": "no header"})
            return

        try:
            header = json.loads(body[:split].decode("utf-8"))
        except ValueError:
            self.reply(400, {"error": "bad header"})
            return

        if header.get("login", "") != self.headers.get("X-De-Login", ""):
            self.reply(403, {"error": "login mismatch"})
            return

        offset = split + 1
        blobs = []
        for piece in header.get("pieces", []):
            size = int(piece[1])
            blobs.append(body[offset:offset + size])
            offset += size

        thumb_size = int(header.get("thumb", 0))
        if thumb_size:
            blobs.append(body[offset:offset + thumb_size])

        try:
            store(header, blobs)
        except OSError as error:
            self.reply(500, {"error": str(error)})
            return

        login = safe_login(header["login"])
        self.reply(200, {"ok": True, "interval": MODES.get(login, 0)})

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        parts = parsed.path.strip("/").split("/")

        if parts[0] == "commands":
            secret = read_secret()
            if not secret or self.headers.get("X-De-Secret") != secret:
                self.reply(403, {"error": "bad secret"})
                return
            login = safe_login(query.get("login", [""])[0])
            wait = min(60, max(1, int(query.get("wait", ["25"])[0])))
            if not login:
                self.reply(400, {"error": "no login"})
                return
            deadline = time.time() + wait
            while time.time() < deadline:
                queue = QUEUES.get(login)
                if queue:
                    QUEUES[login] = []
                    self.reply(200, {"commands": queue})
                    return
                time.sleep(0.5)
            self.reply(200, {"commands": []})
            return

        if parts[0] == "results" and len(parts) == 2:
            batch = parts[1]
            self.reply(200, {"batch": batch, "results": RESULTS.get(batch, [])})
            return

        if parts[0] == "batch":
            secret = read_secret()
            if not secret or self.headers.get("X-De-Secret") != secret:
                self.reply(403, {"error": "bad secret"})
                return
            name = query.get("name", [""])[0]
            logins = [safe_login(item) for item in query.get("login", [])]
            if not name or not logins:
                self.reply(400, {"error": "need name and login"})
                return
            COUNTER["value"] += 1
            batch = "b%d-%d" % (int(time.time()), COUNTER["value"])
            RESULTS[batch] = []
            sent = []
            for login in logins:
                command = {"id": batch + ":" + login, "batch": batch, "name": name}
                if "interval" in query:
                    command["interval"] = int(query["interval"][0])
                if "text" in query:
                    command["text"] = query["text"][0]
                if "delay" in query:
                    command["delay"] = query["delay"][0]
                QUEUES.setdefault(login, []).append(command)
                sent.append(login)
                if name == "live":
                    MODES[login] = command.get("interval", 1)
                if name == "archive":
                    MODES.pop(login, None)
            self.reply(200, {"ok": True, "batch": batch, "sent": sent})
            return

        if parts[0] == "command" and len(parts) == 2:
            login = safe_login(parts[1])
            name = query.get("name", [""])[0]
            if not name:
                self.reply(400, {"error": "no name"})
                return
            command = {"name": name}
            if "interval" in query:
                command["interval"] = int(query["interval"][0])
            if "text" in query:
                command["text"] = query["text"][0]
            QUEUES.setdefault(login, []).append(command)
            if name == "live":
                MODES[login] = command.get("interval", 1)
            if name == "archive":
                MODES.pop(login, None)
            self.reply(200, {"ok": True, "queued": command})
            return

        if parts[0] == "thumb" and len(parts) == 2:
            path = os.path.join(DATA_DIR, "thumb-" + safe_login(parts[1]) + ".webp")
            if not os.path.exists(path):
                self.reply(404, {"error": "no thumb"})
                return
            data = open(path, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "image/webp")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if parts[0] == "latest" and len(parts) == 2:
            path = os.path.join(DATA_DIR, "latest-" + safe_login(parts[1]) + ".json")
            if not os.path.exists(path):
                self.reply(404, {"error": "no frames"})
                return
            self.reply(200, json.loads(open(path).read()))
            return

        if parts[0] == "live" and len(parts) == 3:
            login = safe_login(parts[1])
            MODES[login] = max(1, int(parts[2]))
            self.reply(200, {"ok": True, "login": login, "interval": MODES[login]})
            return

        if parts[0] == "piece" and len(parts) == 5:
            login = safe_login(parts[1])
            path = os.path.join(DATA_DIR, parts[2], login, parts[3], os.path.basename(parts[4]))
            if not os.path.exists(path) or not path.endswith(".webp"):
                self.reply(404, {"error": "no piece"})
                return
            data = open(path, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "image/webp")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        self.reply(404, {"error": "unknown path"})


os.makedirs(DATA_DIR, exist_ok=True)
ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
