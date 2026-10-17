#!/usr/bin/python3

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

DATA_DIR = os.environ.get("DE_SCREEN_DATA", "/data/digital-examen/screens")
RECODE = os.environ.get("DE_SCREEN_RECODE", "/usr/local/bin/recode.py")
PYTHON = os.environ.get("DE_SCREEN_PYTHON", "/usr/bin/python3")

STORE_WIDTH = int(os.environ.get("DE_SCREEN_STORE_WIDTH", "1024"))
STORE_HEIGHT = int(os.environ.get("DE_SCREEN_STORE_HEIGHT", "576"))

DISK_LIMIT_GB = int(os.environ.get("DE_SCREEN_LIMIT_GB", "900"))
NOTIFY_URL = os.environ.get("DE_NOTIFY_URL", "http://de-alerts:8099/notify")
NOTIFY_SECRET_FILE = os.environ.get("DE_NOTIFY_SECRET_FILE", "/run/secrets/notify.secret")
API_URL = os.environ.get("DE_API_URL", "")
API_SECRET_FILE = os.environ.get("DE_SCREENS_SECRET_FILE", "/run/secrets/proxy.secret")

LADDER = ((28, "delete"), (21, 8), (14, 6), (0, 4))


def say(message):
    sys.stdout.write(message + "\n")
    sys.stdout.flush()


def read_file(path):
    if not os.path.exists(path):
        return ""
    return open(path).read().strip()


def notify(text):
    secret = read_file(NOTIFY_SECRET_FILE)
    if not secret or not NOTIFY_URL:
        say("уведомление не отправлено: нет секрета или адреса")
        return
    body = json.dumps({"text": text, "important": True, "force": True}).encode()
    request = urllib.request.Request(NOTIFY_URL, data=body)
    request.add_header("Content-Type", "application/json")
    request.add_header("X-De-Secret", secret)
    try:
        urllib.request.urlopen(request, timeout=20).read()
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as error:
        say("уведомление не доставлено: %s" % error)


def tell_site_dropped(day):
    secret = read_file(API_SECRET_FILE)
    if not API_URL or not secret:
        return
    body = json.dumps({"day": day}).encode()
    request = urllib.request.Request(API_URL.rstrip("/") + "/api/computers/device/dropped", data=body)
    request.add_header("Content-Type", "application/json")
    request.add_header("X-De-Secret", secret)
    try:
        urllib.request.urlopen(request, timeout=20).read()
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as error:
        say("сайту не сообщили про %s: %s" % (day, error))


def days():
    if not os.path.isdir(DATA_DIR):
        return []
    out = []
    for name in sorted(os.listdir(DATA_DIR)):
        path = os.path.join(DATA_DIR, name)
        if not os.path.isdir(path) or len(name) != 10 or name.count("-") != 2:
            continue
        out.append(name)
    return out


def age_of(day):
    try:
        stamp = time.mktime(time.strptime(day, "%Y-%m-%d"))
    except ValueError:
        return -1
    return int((time.time() - stamp) // 86400)


def wanted_step(age):
    for limit, step in LADDER:
        if age >= limit:
            return step
    return 4


def folder_size(path):
    total = 0
    for root, _, names in os.walk(path):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def records_of(day):
    out = []
    base = os.path.join(DATA_DIR, day)
    for computer in sorted(os.listdir(base)):
        room = os.path.join(base, computer)
        if not os.path.isdir(room):
            continue
        for login in sorted(os.listdir(room)):
            folder = os.path.join(room, login)
            if os.path.isdir(folder):
                out.append(folder)
    return out


def read_record(folder):
    path = os.path.join(folder, "record.json")
    if not os.path.exists(path):
        return {}
    try:
        return json.loads(open(path).read())
    except ValueError:
        return {}


def recode(folder, width, height, step):
    return subprocess.call(
        ["nice", "-n", "19", PYTHON, RECODE, folder, str(width), str(height), str(step)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ) == 0


def shrink_and_thin():
    for day in days():
        age = age_of(day)
        if age < 0:
            continue

        want = wanted_step(age)
        if want == "delete":
            size = folder_size(os.path.join(DATA_DIR, day))
            subprocess.call(["rm", "-rf", os.path.join(DATA_DIR, day)])
            tell_site_dropped(day)
            say("удалён %s: возраст %d дней, освобождено %d байт" % (day, age, size))
            continue

        for folder in records_of(day):
            record = read_record(folder)
            have = int(record.get("step", 2) or 2)
            width = int(record.get("width", STORE_WIDTH) or STORE_WIDTH)
            height = int(record.get("height", STORE_HEIGHT) or STORE_HEIGHT)

            factor = max(1, want // have) if have else 1
            too_big = width > STORE_WIDTH or height > STORE_HEIGHT

            if factor < 2 and not too_big:
                continue

            if recode(folder, STORE_WIDTH, STORE_HEIGHT, factor):
                say("%s: %dx%d раз в %d с -> %dx%d раз в %d с" % (
                    folder, width, height, have, STORE_WIDTH, STORE_HEIGHT, have * factor))
            else:
                say("%s: перекодировать не удалось" % folder)


def enforce_limit():
    limit = DISK_LIMIT_GB * 1024 ** 3
    total = folder_size(DATA_DIR)
    if total <= limit:
        return

    dropped = []
    while total > limit:
        left = days()
        if len(left) <= 1:
            break
        oldest = left[0]
        size = folder_size(os.path.join(DATA_DIR, oldest))
        subprocess.call(["rm", "-rf", os.path.join(DATA_DIR, oldest)])
        tell_site_dropped(oldest)
        dropped.append(oldest)
        total -= size
        say("лимит: удалён %s, освобождено %d байт" % (oldest, size))

    if dropped:
        notify(
            "<b>Записи экранов: превышено ограничение диска</b>\n"
            "Отведено %d ГБ, занято было больше.\n"
            "Удалены самые старые дни: %s\n"
            "Сейчас занято %.1f ГБ."
            % (DISK_LIMIT_GB, ", ".join(dropped), total / float(1024 ** 3))
        )


def main():
    if not os.path.isdir(DATA_DIR):
        return 0
    shrink_and_thin()
    enforce_limit()
    return 0


sys.exit(main())
