#!/usr/bin/python3

import json
import os
import sys
import time
import urllib.error
import urllib.request

DATA_DIR = os.environ.get("DE_SCREEN_DATA", "/data/digital-examen/screens")
LIVE_DIR = os.environ.get("DE_SCREEN_LIVE", "/run/digital-examen/live")
SEEN_SECONDS = int(os.environ.get("DE_SEEN_SECONDS", "90"))
API_URL = os.environ.get("DE_API_URL", "")
SECRET_FILE = os.environ.get("DE_SCREENS_SECRET_FILE", "/run/secrets/proxy.secret")


def read_secret():
    if os.path.exists(SECRET_FILE):
        return open(SECRET_FILE).read().strip()
    return os.environ.get("DE_SCREENS_SECRET", "")


def post(path, payload):
    body = json.dumps(payload).encode()
    request = urllib.request.Request(API_URL.rstrip("/") + path, data=body)
    request.add_header("Content-Type", "application/json")
    request.add_header("X-De-Secret", read_secret())
    answer = urllib.request.urlopen(request, timeout=20)
    return json.loads(answer.read().decode("utf-8"))


def folder_size(path):
    total = 0
    for root, _, names in os.walk(path):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def walk_records():
    if not os.path.isdir(DATA_DIR):
        return

    usage = {}
    for day in sorted(os.listdir(DATA_DIR)):
        base = os.path.join(DATA_DIR, day)
        if not os.path.isdir(base) or len(day) != 10:
            continue
        for computer in sorted(os.listdir(base)):
            room = os.path.join(base, computer)
            if not os.path.isdir(room):
                continue
            usage[computer] = usage.get(computer, 0) + folder_size(room)
            for login in sorted(os.listdir(room)):
                folder = os.path.join(room, login)
                if os.path.isdir(folder):
                    yield computer, folder
    for computer, total in usage.items():
        send_usage(computer, total)


def send_usage(computer, total):
    try:
        post("/api/computers/device/usage", {
            "computer": computer,
            "saved_bytes": total,
        })
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        pass


def send_record(computer, folder):
    path = os.path.join(folder, "record.json")
    if not os.path.exists(path):
        return
    try:
        record = json.loads(open(path).read())
    except ValueError:
        return

    payload = {
        "computer": computer,
        "login": record.get("login", ""),
        "started": int(record.get("started", 0)),
        "seconds": int(record.get("seconds", 0)),
        "bytes": int(record.get("bytes", 0)),
        "step": int(record.get("step", 2)),
        "width": int(record.get("width", 1024)),
        "height": int(record.get("height", 576)),
    }

    try:
        answer = post("/api/computers/device/record", payload)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return

    identifier = answer.get("id")
    if identifier is None or str(record.get("id", "")) == str(identifier):
        return

    record["id"] = identifier
    out = open(path, "w")
    out.write(json.dumps(record))
    out.close()


def send_seen():
    """Кто сейчас шлёт кадры — тот и включён."""
    if not os.path.isdir(LIVE_DIR):
        return

    alive = []
    now = time.time()
    for name in sorted(os.listdir(LIVE_DIR)):
        if not name.endswith(".seen"):
            continue
        path = os.path.join(LIVE_DIR, name)
        try:
            if now - os.path.getmtime(path) <= SEEN_SECONDS:
                alive.append(name[:-5])
        except OSError:
            continue

    try:
        post("/api/computers/device/seen", {"computers": alive})
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        pass


def main():
    if not API_URL:
        return 0

    send_seen()
    for computer, folder in walk_records():
        send_record(computer, folder)
    return 0


sys.exit(main())
