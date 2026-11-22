import fcntl
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

HISTORY = Path("/run/digital-examen-blocks/history.json")


def read():
    try:
        return json.loads(HISTORY.read_text())
    except (OSError, ValueError):
        return []


def remember(url, reason, group="", trigger=""):
    try:
        parts = urlsplit(url)
        url = parts._replace(query="", fragment="").geturl()
        lock = HISTORY.with_suffix(".lock")
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            entries = read()[-49:]
            entries.append([int(time.time()), url[:2048], reason, group, trigger])
            temp = HISTORY.with_suffix(".new")
            fd = os.open(temp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as output:
                json.dump(entries, output, ensure_ascii=False)
            os.replace(temp, HISTORY)
    except (OSError, ValueError):
        pass
