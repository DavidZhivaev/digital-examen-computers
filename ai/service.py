#!/usr/bin/python3

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import markers
import model

PORT = int(os.environ.get("DE_AI_PORT", "9850"))
SECRET_FILE = os.environ.get("DE_AI_SECRET_FILE", "/run/secrets/ai.secret")
THRESHOLD = float(os.environ.get("DE_AI_THRESHOLD", "0.75"))
MARKER_SURE = float(os.environ.get("DE_AI_MARKER_SURE", "6"))
CACHE_SECONDS = int(os.environ.get("DE_AI_CACHE_SECONDS", "600"))
CACHE_LIMIT = int(os.environ.get("DE_AI_CACHE_LIMIT", "20000"))
DOUBT_FILE = os.environ.get("DE_AI_DOUBT_FILE", "/models/doubt.jsonl")
DOUBT_LOW = float(os.environ.get("DE_AI_DOUBT_LOW", "0.35"))
DOUBT_HIGH = float(os.environ.get("DE_AI_DOUBT_HIGH", "0.75"))

# Пока модели нет, копим настоящие страницы: по одной с узла в час и не
# больше заданного числа за сутки. Из них и соберётся первая настоящая
# выборка вместо придуманной.
COLLECT = os.environ.get("DE_AI_COLLECT", "1") == "1"
COLLECT_PER_DAY = int(os.environ.get("DE_AI_COLLECT_PER_DAY", "500"))
COLLECT_HOST_SECONDS = int(os.environ.get("DE_AI_COLLECT_HOST_SECONDS", "3600"))

SEEN_HOSTS = {}
COLLECTED = [0, 0.0]

LOCK = threading.Lock()
CACHE = {}
MACHINE = [None, {}]


def read_secret():
    if os.path.exists(SECRET_FILE):
        return open(SECRET_FILE).read().strip()
    return os.environ.get("DE_AI_SECRET", "")


def same(first, second):
    if not first or not second or len(first) != len(second):
        return False
    bad = 0
    for one, two in zip(first, second):
        bad |= ord(one) ^ ord(two)
    return bad == 0


def cache_get(key):
    with LOCK:
        found = CACHE.get(key)
        if not found:
            return None
        stamp, answer = found
        if time.time() - stamp > CACHE_SECONDS:
            CACHE.pop(key, None)
            return None
        return answer


def cache_put(key, answer):
    with LOCK:
        if len(CACHE) >= CACHE_LIMIT:
            CACHE.clear()
        CACHE[key] = (time.time(), answer)


def note_doubt(sample, chance):
    """Спорные страницы складываются для ночной разметки большой моделью.

    Сама модель ничего не блокирует и ничего не решает: она отвечает только
    «ИИ или не ИИ» с уверенностью и цитатой. Спорное уходит сюда, ночью его
    разметит Qwen, а утром на этом доучится логистическая регрессия.
    """
    if not (DOUBT_LOW <= chance <= DOUBT_HIGH):
        return
    try:
        out = open(DOUBT_FILE, "a")
        out.write(json.dumps({
            "host": sample.get("host", ""),
            "url": sample.get("url", ""),
            "title": sample.get("title", ""),
            "text": model.clean(sample.get("text", ""))[:1200],
            "chance": round(chance, 4),
            "at": int(time.time()),
        }, ensure_ascii=False) + "\n")
        out.close()
    except OSError:
        pass


def collect(sample):
    """Отложить настоящую страницу на будущую разметку.

    Пишем только то, что и так уходит классификатору: узел, адрес,
    заголовок и обрезанный текст. Ни логина, ни компьютера здесь нет —
    выборка должна учить отличать страницы, а не людей.
    """
    if not COLLECT or not sample.get("text"):
        return

    host = str(sample.get("host", ""))
    now = time.time()

    with LOCK:
        day = int(now // 86400)
        if COLLECTED[1] != day:
            COLLECTED[0] = 0
            COLLECTED[1] = day
        if COLLECTED[0] >= COLLECT_PER_DAY:
            return
        if now - SEEN_HOSTS.get(host, 0) < COLLECT_HOST_SECONDS:
            return
        SEEN_HOSTS[host] = now
        COLLECTED[0] += 1
        if len(SEEN_HOSTS) > 20000:
            SEEN_HOSTS.clear()

    try:
        out = open(DOUBT_FILE, "a")
        out.write(json.dumps({
            "host": host,
            "url": str(sample.get("url", ""))[:300],
            "title": str(sample.get("title", ""))[:200],
            "text": model.clean(sample.get("text", ""))[:1200],
            "chance": None,
            "at": int(now),
        }, ensure_ascii=False) + "\n")
        out.close()
    except OSError:
        pass


def classify(sample):
    host = str(sample.get("host", ""))
    url = str(sample.get("url", ""))
    title = str(sample.get("title", ""))
    text = str(sample.get("text", ""))

    if markers.never_block(host):
        return {"ai": False, "confidence": 1.0, "quote": "",
                "reason": "never-block", "model": False}

    total, found, quote = markers.score(host, url, title, text)

    if total >= MARKER_SURE:
        return {"ai": True, "confidence": 1.0, "quote": quote,
                "reason": "признаки", "model": False}

    machine = MACHINE[0]
    if machine is None:
        collect(sample)
        return {"ai": False, "confidence": round(min(0.6, total / MARKER_SURE), 3),
                "quote": quote, "reason": "только признаки, модель не обучена", "model": False}

    rows = model.build_rows([{"host": host, "url": url, "title": title, "text": text}])
    chance = float(machine.predict_proba(rows)[0][1])
    note_doubt(sample, chance)

    return {
        "ai": chance >= THRESHOLD,
        "confidence": round(chance, 4),
        "quote": quote,
        "reason": "модель",
        "model": True,
    }


class Handler(BaseHTTPRequestHandler):

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        return

    def reply(self, code, payload):
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/") == "/health":
            self.reply(200, {"ok": True, "model": MACHINE[0] is not None, "info": MACHINE[1]})
            return
        self.reply(404, {"error": "unknown path"})

    def do_POST(self):
        if self.path.rstrip("/") != "/classify":
            self.reply(404, {"error": "unknown path"})
            return

        known = read_secret()
        if known and not same(self.headers.get("X-De-Secret", ""), known):
            self.reply(403, {"error": "bad secret"})
            return

        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 4 * 1024 * 1024:
            self.reply(400, {"error": "bad length"})
            return

        try:
            sample = json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            self.reply(400, {"error": "bad json"})
            return

        key = str(sample.get("host", "")) + "|" + str(sample.get("url", ""))[:200]
        answer = cache_get(key)
        if answer is None:
            started = time.time()
            answer = classify(sample)
            answer["ms"] = int((time.time() - started) * 1000)
            cache_put(key, answer)

        self.reply(200, answer)


def main():
    MACHINE[0], MACHINE[1] = model.load()
    if MACHINE[0] is not None:
        model.embedder()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


main()
