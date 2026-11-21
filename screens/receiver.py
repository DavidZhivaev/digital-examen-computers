#!/usr/bin/python3

import fcntl
import json
import os
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import space

DATA_DIR = os.environ.get("DE_SCREEN_DATA", "/data/digital-examen/screens")
LIVE_DIR = os.environ.get("DE_SCREEN_LIVE", "/run/digital-examen/live")
STATE_DIR = os.environ.get("DE_SCREEN_STATE", "/data/digital-examen/state")
GLUE = os.environ.get("DE_SCREEN_GLUE", "/usr/local/bin/glue.py")
PYTHON = os.environ.get("DE_SCREEN_PYTHON", "/usr/bin/python3")

SECRET_FILE = os.environ.get("DE_SCREEN_SECRET", "/etc/digital-examen/proxy.secret")
COMMAND_SECRET_FILE = os.environ.get("DE_COMMAND_SECRET", "")
SECRETS_DIR = os.environ.get("DE_SECRETS_DIR", "/data/digital-examen/secrets")
ALLOW_SHARED = os.environ.get("DE_ALLOW_SHARED_SECRET", "1") == "1"
PORT = int(os.environ.get("DE_SCREEN_PORT", "9840"))
MIN_FREE_GB = int(os.environ.get("DE_SCREEN_MIN_FREE_GB", "50"))
SECRET_GRACE_SECONDS = int(os.environ.get("DE_SECRET_GRACE_HOURS", "24")) * 3600
SPACE_EVERY = int(os.environ.get("DE_SCREEN_SPACE_EVERY", "200"))
CONSOLE_WAIT = int(os.environ.get("DE_CONSOLE_WAIT", "45"))

WATCHED = {}
QUEUES = {}
RESULTS = {}
STATS = {}
POLICY = {}
COUNTER = {"value": 0}
SPACE = {"frames": 0}
LOCK = threading.Lock()


def read_secret():
    if not os.path.exists(SECRET_FILE):
        return ""
    return open(SECRET_FILE).read().strip()


def read_command_secret():
    if COMMAND_SECRET_FILE and os.path.exists(COMMAND_SECRET_FILE):
        return open(COMMAND_SECRET_FILE).read().strip()
    return read_secret()


def computer_secrets(computer):
    """Секреты именно этого компьютера: текущий и, пока идёт смена, прежний.

    Общий секрет на всю школу означал, что компьютер своим секретом мог
    прочитать кадры соседнего. Теперь у каждой машины свой, сайт приносит
    его сюда при постановке на учёт. Общий остаётся только как переходный,
    пока все машины не переставлены (DE_ALLOW_SHARED_SECRET=0 его закрывает).
    """
    name = safe_name(computer)
    path = os.path.join(SECRETS_DIR, name + ".json")
    if not name or not os.path.exists(path):
        return []
    try:
        values = json.loads(open(path).read())
    except (ValueError, OSError):
        return []

    if values.get("revoked"):
        return None
    out = [str(values.get("screen", ""))]
    rotated = int(values.get("rotated", 0) or 0)
    if values.get("previous") and time.time() - rotated <= SECRET_GRACE_SECONDS:
        out.append(str(values["previous"]))
    return [item for item in out if item]


def save_computer_secrets(computer, values):
    name = safe_name(computer)
    if not name:
        return False
    os.makedirs(SECRETS_DIR, exist_ok=True)
    os.chmod(SECRETS_DIR, 0o700)
    path = os.path.join(SECRETS_DIR, name + ".json")
    old = ""
    if os.path.exists(path) and not values.get("revoked"):
        try:
            old = str(json.loads(open(path).read()).get("screen", ""))
        except (ValueError, OSError):
            old = ""

    out = open(path + ".new", "w")
    out.write(json.dumps({
        "screen": str(values.get("screen", "")),
        "revoked": bool(values.get("revoked")),
        "previous": old,
        "rotated": int(time.time()),
    }))
    out.close()
    os.chmod(path + ".new", 0o600)
    os.replace(path + ".new", path)
    return True


def same(first, second):
    if not first or not second or len(first) != len(second):
        return False
    bad = 0
    for one, two in zip(first, second):
        bad |= ord(one) ^ ord(two)
    return bad == 0


def safe_name(value):
    out = ""
    for char in str(value).lower():
        if char.isalnum() or char in "_-":
            out += char
    return out


def login_dir(computer, login, stamp):
    day = time.strftime("%Y-%m-%d", time.localtime(stamp))
    return os.path.join(DATA_DIR, day, safe_name(computer), safe_name(login) or "unknown")


def note_seen(computer):
    """Отметка «кадры идут».

    Включён компьютер или нет, решается именно по этому: пока он шлёт кадры,
    он жив. Отдельному сердцебиению верить нельзя - оно может доходить и
    тогда, когда экран давно не снимается.
    """
    name = safe_name(computer)
    if not name:
        return
    try:
        os.makedirs(LIVE_DIR, exist_ok=True)
        path = os.path.join(LIVE_DIR, name + ".seen")
        open(path, "w").close()
    except OSError:
        pass


def store_live(header, blob):
    computer = safe_name(header.get("computer", ""))
    if not computer or not blob:
        return
    os.makedirs(LIVE_DIR, exist_ok=True)
    path = os.path.join(LIVE_DIR, computer + ".webp")
    temporary = path + ".new"
    out = open(temporary, "wb")
    out.write(blob)
    out.close()
    os.replace(temporary, path)


def store_frame(header, blobs):
    computer = safe_name(header.get("computer", ""))
    login = header.get("login", "")
    if not computer:
        return 0

    folder = login_dir(computer, login, header["time"])
    path = os.path.join(folder, "%010d" % header["frame"])
    os.makedirs(path, exist_ok=True)

    written = 0
    names = []
    for position, piece in enumerate(header["pieces"]):
        if piece[0] < 0:
            name = "full.webp"
        else:
            name = "r%d_%d_%d.webp" % (piece[0], piece[1], piece[2])
        out = open(os.path.join(path, name), "wb")
        out.write(blobs[position])
        out.close()
        written += len(blobs[position])
        names.append(name)

    meta = {
        "computer": computer,
        "login": login,
        "frame": header["frame"],
        "time": header["time"],
        "size": header["size"],
        "grid": header["grid"],
        "tile": header["tile"],
        "keyframe": header["keyframe"],
        "pieces": [piece[:3] for piece in header["pieces"]],
        "files": names,
    }
    out = open(os.path.join(path, "meta.json"), "w")
    out.write(json.dumps(meta))
    out.close()

    with open(os.path.join(folder, ".record.lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        note_record(folder, header, written)
    return written


def note_record(folder, header, written):
    path = os.path.join(folder, "record.json")
    record = {
        "computer": safe_name(header.get("computer", "")),
        "login": header.get("login", ""),
        "started": header["time"],
        "last": header["time"],
        "frames": 0,
        "bytes": 0,
        "step": 2,
        "width": header["size"][0],
        "height": header["size"][1],
    }
    if os.path.exists(path):
        try:
            record.update(json.loads(open(path).read()))
        except ValueError:
            pass

    record["last"] = max(int(record["last"]), header["time"])
    record["frames"] = int(record.get("frames", 0)) + 1
    record["bytes"] = int(record.get("bytes", 0)) + written
    record["seconds"] = max(0, record["last"] - record["started"])

    with open(path + ".new", "w") as out:
        json.dump(record, out)
    os.replace(path + ".new", path)


def watch_space():
    """Место кончается - удаляем самое старое, не переставая принимать.

    Нельзя допустить, чтобы запись прекратилась из-за полного диска:
    поэтому проверка идёт прямо в приёмнике, а не только ночью.
    """
    with LOCK:
        SPACE["frames"] += 1
        if SPACE["frames"] % SPACE_EVERY != 0:
            return
    need = MIN_FREE_GB * 1024 ** 3
    if space.free_bytes(DATA_DIR) >= need:
        return
    space.free_up(DATA_DIR, need)


def queue_command(computer, command):
    with LOCK:
        QUEUES.setdefault(safe_name(computer), []).append(command)


class Handler(BaseHTTPRequestHandler):

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        return

    def reply(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_blob(self, data, kind):
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def allowed(self, command_level):
        sent = self.headers.get("X-De-Secret", "")
        if command_level:
            return same(sent, read_command_secret())
        return same(sent, read_secret()) or same(sent, read_command_secret())

    def allowed_computer(self, computer):
        own = computer_secrets(computer)
        if own is None:
            return False
        sent = self.headers.get("X-De-Secret", "")
        if any(same(sent, secret) for secret in own):
            return True
        if same(sent, read_command_secret()):
            return True
        if ALLOW_SHARED and same(sent, read_secret()):
            return True
        return False

    def body_json(self, limit=1024 * 1024):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > limit:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return None

    def do_POST(self):
        route = urlparse(self.path).path.rstrip("/")
        query = parse_qs(urlparse(self.path).query)

        if route == "/frame":
            self.take_frame()
            return

        if route == "/secrets":
            if not self.allowed(True):
                self.reply(403, {"error": "bad secret"})
                return
            payload = self.body_json()
            if payload is None:
                self.reply(400, {"error": "bad json"})
                return
            computer = safe_name(payload.get("computer", ""))
            with LOCK:
                saved = save_computer_secrets(computer, payload)
                if saved and payload.get("revoked"):
                    for cache in (WATCHED, QUEUES, STATS, POLICY):
                        cache.pop(computer, None)
                    for suffix in (".webp", ".seen"):
                        try:
                            os.unlink(os.path.join(LIVE_DIR, computer + suffix))
                        except FileNotFoundError:
                            pass
            if not saved:
                self.reply(400, {"error": "bad computer"})
                return
            self.reply(200, {"ok": True, "revoked": bool(payload.get("revoked"))})
            return

        if route == "/result":
            if not self.allowed_computer(self.headers.get("X-De-Computer", "")):
                self.reply(403, {"error": "bad secret"})
                return
            payload = self.body_json()
            if payload is None:
                self.reply(400, {"error": "bad json"})
                return
            with LOCK:
                RESULTS[str(payload.get("id", ""))] = payload
            self.reply(200, {"ok": True})
            return

        if route == "/stats":
            if not self.allowed_computer(self.headers.get("X-De-Computer", "")):
                self.reply(403, {"error": "bad secret"})
                return
            payload = self.body_json()
            if payload is None:
                self.reply(400, {"error": "bad json"})
                return
            computer = safe_name(payload.get("computer", ""))
            if computer:
                payload["time"] = int(time.time())
                with LOCK:
                    STATS[computer] = payload
            self.reply(200, {"ok": True})
            return

        if route in ("/console", "/close-tab"):
            if not self.allowed(True):
                self.reply(403, {"error": "bad secret"})
                return
            self.run_console(safe_name(query.get("computer", [""])[0]), route == "/close-tab")
            return

        parts = route.strip("/").split("/")
        if parts[0] == "policy" and len(parts) == 2:
            if not self.allowed(True):
                self.reply(403, {"error": "bad secret"})
                return
            payload = self.body_json(limit=4 * 1024 * 1024)
            if payload is None:
                self.reply(400, {"error": "bad json"})
                return
            computer = safe_name(parts[1])
            with LOCK:
                POLICY[computer] = payload
            queue_command(computer, {"name": "policy"})
            self.reply(200, {"ok": True})
            return

        self.reply(404, {"error": "unknown path"})

    def take_frame(self):
        if not self.allowed_computer(self.headers.get("X-De-Computer", "")):
            self.reply(403, {"error": "bad secret"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 32 * 1024 * 1024:
            self.reply(400, {"error": "bad length"})
            return

        body = self.rfile.read(length)
        if not self.allowed_computer(self.headers.get("X-De-Computer", "")):
            self.reply(403, {"error": "bad secret"})
            return
        split = body.find(b"\n")
        if split < 0:
            self.reply(400, {"error": "no header"})
            return

        try:
            header = json.loads(body[:split].decode("utf-8"))
        except ValueError:
            self.reply(400, {"error": "bad header"})
            return

        if header.get("computer", "") != self.headers.get("X-De-Computer", ""):
            self.reply(403, {"error": "computer mismatch"})
            return

        offset = split + 1
        blobs = []
        for piece in header.get("pieces", []):
            size = int(piece[-1])
            blobs.append(body[offset:offset + size])
            offset += size

        note_seen(header.get("computer", ""))
        try:
            if header.get("kind") == "live":
                store_live(header, blobs[0] if blobs else b"")
            else:
                watch_space()
                store_frame(header, blobs)
        except (OSError, IndexError, KeyError) as error:
            self.reply(500, {"error": str(error)})
            return

        self.reply(200, {"ok": True, "interval": watched_interval(header.get("computer", ""))})

    def run_console(self, computer, close_tab=False):
        payload = self.body_json()
        if payload is None or not computer:
            self.reply(400, {"error": "bad request"})
            return

        with LOCK:
            COUNTER["value"] += 1
            identifier = "c%d-%d" % (int(time.time()), COUNTER["value"])

        queue_command(computer, {
            "id": identifier,
            "name": "close-tab" if close_tab else "exec",
            "tab": str(payload.get("tab", "")) if close_tab else "",
            "text": "" if close_tab else str(payload.get("command", "")),
        })

        deadline = time.time() + CONSOLE_WAIT
        while time.time() < deadline:
            with LOCK:
                answer = RESULTS.pop(identifier, None)
            if answer is not None:
                self.reply(200, {
                    "code": int(answer.get("code", 1)),
                    "output": str(answer.get("output", "")),
                })
                return
            time.sleep(0.3)

        self.reply(200, {"code": 124, "output": "компьютер не ответил за %d с" % CONSOLE_WAIT})

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        parts = parsed.path.strip("/").split("/")

        if parts[0] == "health":
            self.reply(200, {"ok": True})
            return

        if parts[0] == "commands":
            if not self.allowed_computer(query.get("computer", [""])[0]):
                self.reply(403, {"error": "bad secret"})
                return
            self.long_poll(query)
            return

        command_level = parts[0] in ("watch", "power", "record", "shot")
        if not self.allowed(command_level):
            self.reply(403, {"error": "bad secret"})
            return

        if parts[0] == "watch":
            interval = 1 if query.get("interval", ["2"])[0] == "1" else 2
            names = [safe_name(item) for item in query.get("computer", [])]
            now = time.monotonic()
            with LOCK:
                for name in filter(None, names):
                    previous = active_watch(name, now)
                    WATCHED.setdefault(name, {})[interval] = now + 12
                    current = active_watch(name, now)
                    if previous != current:
                        queue = QUEUES.setdefault(name, [])
                        queue[:] = [command for command in queue if command.get("name") != "live"]
                        queue.append({"name": "live", "interval": current})
            self.reply(200, {"ok": True, "watched": names, "interval": interval})
            return

        if parts[0] == "power":
            name = query.get("name", [""])[0]
            if name not in ("reboot", "shutdown"):
                self.reply(400, {"error": "bad action"})
                return
            sent = []
            for item in query.get("computer", []):
                computer = safe_name(item)
                if not computer:
                    continue
                queue_command(computer, {"name": "exec", "text": "poweroff" if name == "shutdown" else "reboot"})
                sent.append(computer)
            self.reply(200, {"ok": True, "sent": sent})
            return

        if parts[0] == "shot" and len(parts) == 2:
            path = os.path.join(LIVE_DIR, safe_name(parts[1]) + ".webp")
            if not os.path.exists(path) or time.time() - os.path.getmtime(path) > 12:
                self.reply(404, {"error": "no fresh shot"})
                return
            self.send_blob(open(path, "rb").read(), "image/webp")
            return

        if parts[0] == "stats" and len(parts) == 2:
            with LOCK:
                self.reply(200, STATS.get(safe_name(parts[1]), {}))
            return

        if parts[0] == "record" and len(parts) == 3:
            self.send_record(safe_name(parts[1]), parts[2], query)
            return

        self.reply(404, {"error": "unknown path"})

    def long_poll(self, query):
        computer = safe_name(query.get("computer", [""])[0])
        wait = min(60, max(1, int(query.get("wait", ["25"])[0])))
        if not computer:
            self.reply(400, {"error": "no computer"})
            return

        deadline = time.time() + wait
        while time.time() < deadline:
            with LOCK:
                queue = QUEUES.get(computer)
                if queue:
                    QUEUES[computer] = []
                    for command in queue:
                        if command.get("name") == "policy":
                            command["policy"] = POLICY.get(computer, {})
            if queue:
                self.reply(200, {"commands": queue, "interval": watched_interval(computer)})
                return
            time.sleep(0.2)

        self.reply(200, {"commands": [], "interval": watched_interval(computer)})

    def send_record(self, computer, record_id, query):
        folder = find_record(computer, record_id, query)
        if folder is None:
            self.reply(404, {"error": "no record"})
            return

        with tempfile.TemporaryDirectory(prefix="de-record-") as work:
            target = os.path.join(work, "video.webm")
            try:
                code = subprocess.call([PYTHON, GLUE, folder, target], timeout=220)
            except subprocess.TimeoutExpired:
                self.reply(504, {"error": "превышено время подготовки записи"})
                return
            if code != 0 or not os.path.isfile(target) or not os.path.getsize(target):
                self.reply(500, {"error": "не удалось склеить запись"})
                return
            with open(target, "rb") as video:
                self.send_blob(video.read(), "video/webm")


def find_record(computer, record_id, query):
    try:
        started = int(query.get("started", ["0"])[0])
        login = query.get("login", [""])[0]
        if started > 0:
            folder = login_dir(computer, login, started)
            with open(os.path.join(folder, "record.json")) as source:
                record = json.load(source)
            if record.get("started") == started and (record.get("login") or "") == login:
                return folder
    except (OSError, ValueError, OverflowError):
        pass

    if not os.path.isdir(DATA_DIR):
        return None
    for day in sorted(os.listdir(DATA_DIR), reverse=True):
        base = os.path.join(DATA_DIR, day, computer)
        if not os.path.isdir(base):
            continue
        for login in sorted(os.listdir(base)):
            folder = os.path.join(base, login)
            path = os.path.join(folder, "record.json")
            if not os.path.exists(path):
                continue
            try:
                record = json.loads(open(path).read())
            except (OSError, ValueError):
                continue
            if str(record.get("id", "")) == str(record_id):
                return folder
    return None


def active_watch(name, now):
    current = WATCHED.get(name, {})
    for interval, expires in list(current.items()):
        if expires <= now:
            current.pop(interval)
    if current:
        return min(current)
    WATCHED.pop(name, None)
    return 0


def watched_interval(computer):
    with LOCK:
        return active_watch(safe_name(computer), time.monotonic())


os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LIVE_DIR, exist_ok=True)
os.makedirs(STATE_DIR, exist_ok=True)
class ReceiverServer(ThreadingHTTPServer):
    request_queue_size = 128


ReceiverServer(("0.0.0.0", PORT), Handler).serve_forever()
