import json
import os
import re
import socket
import struct
import threading
import time
import uuid

SOCKET = "/run/digital-examen/browser-tabs.sock"
TOKEN = re.compile(r"[0-9a-f]{32}")
BROWSERS = {"Chromium", "Chrome", "Edge", "Firefox", "Яндекс", "Opera", "Brave", "Vivaldi"}
READY = threading.Condition()
SESSION = None
RULES = []
URL_GROUPS = []
URL_JSON = "[]"
URL_REVISION = uuid.uuid4().hex
PROFILES = {}
PENDING = {}
ADBLOCKER = False


def activate(session, rules, url_groups, adblocker=False):
    global SESSION, RULES, URL_GROUPS, URL_JSON, URL_REVISION, ADBLOCKER
    with READY:
        if SESSION != session:
            PROFILES.clear()
            PENDING.clear()
        SESSION = session
        RULES = rules
        ADBLOCKER = adblocker
        if URL_GROUPS != url_groups:
            URL_GROUPS = url_groups
            URL_JSON = json.dumps(url_groups, separators=(",", ":"))
            URL_REVISION = uuid.uuid4().hex


def process(uid, data):
    profile = data.get("profile")
    if not isinstance(profile, str) or not TOKEN.fullmatch(profile):
        return {}
    with READY:
        if SESSION is None or SESSION[0] != uid:
            return {}
        now = time.monotonic()
        for key, (stamp, _) in list(PROFILES.items()):
            if stamp + 12 < now:
                del PROFILES[key]
        if profile not in PROFILES and len(PROFILES) >= 24:
            return {}
        browser = data.get("browser")
        tabs = {}
        if isinstance(browser, str) and browser in BROWSERS and isinstance(data.get("tabs"), list):
            for item in data["tabs"][:200]:
                if not isinstance(item, dict):
                    continue
                token, title = item.get("id"), item.get("title")
                if isinstance(token, str) and TOKEN.fullmatch(token) and isinstance(title, str):
                    tabs[token] = {"id": token, "title": title[:500], "browser": browser}
        PROFILES[profile] = (now, tabs)
        for token, success in (data.get("done") or {}).items():
            pending = PENDING.get(token)
            if pending and pending[0] == profile and isinstance(success, bool):
                pending[2] = success
        READY.notify_all()
        response = {"close": [key for key, item in PENDING.items()
                              if item[0] == profile and item[1] > now and item[2] is None]}
        if RULES is not None:
            response["rules"] = RULES
            response["adblocker"] = ADBLOCKER and not os.path.exists("/run/digital-examen/adblocker-off")
        if "url_revision" in data:
            offset = data.get("url_offset")
            if data["url_revision"] != URL_REVISION:
                offset = 0
            if type(offset) is int and 0 <= offset < len(URL_JSON):
                end = offset + 16000
                response["url_policy"] = {
                    "revision": URL_REVISION, "offset": offset,
                    "chunk": URL_JSON[offset:end], "complete": end >= len(URL_JSON),
                }
        return response


def list_tabs():
    with READY:
        now = time.monotonic()
        return [tab for stamp, tabs in PROFILES.values() if stamp + 12 >= now
                for tab in tabs.values()][:500]


def close(token, session_id):
    with READY:
        if SESSION is None or SESSION[1] != session_id or token in PENDING:
            return False
        for profile, (stamp, tabs) in PROFILES.items():
            if token in tabs and stamp + 12 >= time.monotonic():
                item = [profile, time.monotonic() + 8, None]
                PENDING[token] = item
                try:
                    READY.wait_for(lambda: item[2] is not None or token not in PENDING, timeout=8)
                    return item[2] is True
                finally:
                    PENDING.pop(token, None)
        return False


def listen():
    os.makedirs(os.path.dirname(SOCKET), exist_ok=True)
    if os.path.exists(SOCKET):
        os.unlink(SOCKET)
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        server.bind(SOCKET)
        os.chmod(SOCKET, 0o666)
        while True:
            message, controls, flags, sender = server.recvmsg(131072, socket.CMSG_SPACE(12))
            if flags & socket.MSG_TRUNC or not sender:
                continue
            try:
                credentials = next(value for level, kind, value in controls
                                   if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS)
                _, uid, _ = struct.unpack("3i", credentials)
                data = json.loads(message)
                if isinstance(data, dict) and isinstance(data.get("done", {}), dict):
                    server.sendto(json.dumps(process(uid, data)).encode(), sender)
            except (OSError, ValueError, StopIteration):
                continue
