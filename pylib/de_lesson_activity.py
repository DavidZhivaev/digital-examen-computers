"""Per-lesson counters. Input events are reduced locally to covered seconds."""

import glob
import json
import os
from pathlib import Path
import selectors
import struct
import threading
import time


STATE = Path("/var/lib/digital-examen/lesson-activity.json")
DURATION = 2400
EVENT = struct.Struct("@llHHi")
LOCK = threading.Lock()
start = None
covered = requests = 0
privileged = False
privileged_at = 0
sent = None
active = False


def configure(lesson):
    global start, covered, requests, privileged, sent
    with LOCK:
        if lesson == start:
            return
        start = lesson
        covered = requests = 0
        privileged = start is not None and start <= privileged_at < start + DURATION
        sent = None
        if start is None:
            return
        try:
            saved = json.loads(STATE.read_text())
            if saved["start"] == start:
                covered, requests = int(saved["covered"], 16), saved["requests"]
                privileged |= saved["privileged"]
        except (OSError, ValueError, KeyError, TypeError):
            pass


def mark_privileged():
    global privileged, privileged_at
    with LOCK:
        privileged_at = int(time.time())
        if start is not None and start <= privileged_at < start + DURATION:
            privileged = True


def record(stamp, count=0):
    global covered, requests
    with LOCK:
        if start is None:
            return
        offset = int(stamp) - start
        if not 0 <= offset < DURATION:
            return
        requests = min(2**31 - 1, requests + count)
        if not count and active:
            length = min(30, DURATION - offset)
            covered |= ((1 << length) - 1) << offset


def save_state():
    if start is None:
        return
    try:
        temporary = STATE.with_suffix(".tmp")
        temporary.write_text(json.dumps({
            "start": start, "covered": hex(covered), "requests": requests, "privileged": privileged,
        }))
        temporary.replace(STATE)
    except OSError:
        pass


def checkpoint():
    with LOCK:
        save_state()


def snapshot(now):
    with LOCK:
        if start is None:
            return None
        elapsed = min(DURATION, (int(now) - start - 3) // 60 * 60)
        if elapsed < 0 or sent == (start, elapsed):
            return None
        seconds = bin(covered & ((1 << elapsed) - 1)).count("1")
        save_state()
        return {"start": start, "seconds": seconds, "requests": requests, "privileged": privileged}, elapsed


def acknowledged(report):
    global sent
    payload, elapsed = report
    with LOCK:
        sent = (payload["start"], elapsed)


def listen():
    with selectors.DefaultSelector() as selector:
        devices = {}
        held = {}
        position = {}
        refresh = 0
        last = 0
        while True:
            now = time.monotonic()
            if refresh <= now:
                refresh = now + 10
                paths = {os.path.realpath(path) for path in glob.glob("/dev/input/by-path/*-event-*")
                         if path.endswith(("-kbd", "-mouse"))}
                for path in paths - devices.keys():
                    name = os.path.basename(path)
                    if "/virtual/" in os.path.realpath("/sys/class/input/" + name):
                        continue
                    try:
                        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                        selector.register(fd, selectors.EVENT_READ, path)
                    except OSError:
                        continue
                    devices[path] = fd
                    held[fd] = set()
                    position[fd] = {}
            for key, _ in selector.select(timeout=max(0, refresh - time.monotonic())):
                fd = key.fd
                try:
                    data = os.read(fd, EVENT.size * 64)
                except BlockingIOError:
                    continue
                except OSError:
                    data = b""
                if not data:
                    selector.unregister(fd)
                    os.close(fd)
                    devices.pop(key.data, None)
                    held.pop(fd, None)
                    position.pop(fd, None)
                    continue
                activity = False
                for _, _, kind, code, value in EVENT.iter_unpack(data):
                    if kind == 1:
                        if value == 0:
                            held[fd].discard(code)
                        elif value == 1 and code not in held[fd]:
                            held[fd].add(code)
                            activity = True
                    elif kind == 2 and code in (0, 1) and value:
                        activity = True
                    elif kind == 3 and code in (0, 1, 53, 54):
                        previous = position[fd].get(code, value)
                        position[fd][code] = value
                        activity |= previous != value
                stamp = int(time.time())
                if activity and stamp != last:
                    record(stamp=stamp)
                    last = stamp
