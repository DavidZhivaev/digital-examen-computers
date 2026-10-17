#!/usr/bin/python3

import json
import os
import sys
import time
import urllib.error
import urllib.request

OLLAMA = os.environ.get("DE_AI_OLLAMA", "http://127.0.0.1:11434")
MODEL = os.environ.get("DE_AI_OLLAMA_MODEL", "qwen3:4b-instruct-2507-q4_K_M")
DOUBT_FILE = os.environ.get("DE_AI_DOUBT_FILE", "/data/digital-examen/ai/doubt.jsonl")
OUT_FILE = os.environ.get("DE_AI_DATA", "/data/digital-examen/ai/labelled.jsonl")
DONE_FILE = os.environ.get("DE_AI_DONE", "/data/digital-examen/ai/doubt-done.jsonl")
LIMIT = int(os.environ.get("DE_AI_LABEL_LIMIT", "200"))
TIMEOUT = int(os.environ.get("DE_AI_LABEL_TIMEOUT", "180"))

RULES = """Ты размечаешь страницы для школьного фильтра.
ai=true, если страница это разговор с искусственным интеллектом, ответ
нейросети или сервис генерации текста и картинок.
ai=false во всех остальных случаях, включая новости и статьи про ИИ.
Ответь только объектом JSON с полями ai, confidence, quote."""

ASK = """Адрес: %s
Заголовок: %s
Текст: %s"""


def ask(sample):
    body = json.dumps({
        "model": MODEL,
        "stream": False,
        "format": "json",
        "keep_alive": "5m",
        "options": {"temperature": 0, "num_predict": 200},
        "messages": [
            {"role": "system", "content": RULES},
            {"role": "user", "content": ASK % (
                sample.get("url", "")[:300],
                sample.get("title", "")[:200],
                sample.get("text", "")[:1500],
            )},
        ],
    }).encode()

    request = urllib.request.Request(OLLAMA.rstrip("/") + "/api/chat", data=body)
    request.add_header("Content-Type", "application/json")

    try:
        answer = json.loads(urllib.request.urlopen(request, timeout=TIMEOUT).read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as error:
        sys.stdout.write("модель не ответила: %s\n" % error)
        return None

    text = answer.get("message", {}).get("content", "")
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        found = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if "ai" not in found:
        return None
    return found


def read_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def append(path, row):
    out = open(path, "a")
    out.write(json.dumps(row, ensure_ascii=False) + "\n")
    out.close()


def main():
    done = set()
    for row in read_jsonl(DONE_FILE):
        done.add(row.get("url", ""))

    waiting = [row for row in read_jsonl(DOUBT_FILE) if row.get("url", "") not in done]
    if not waiting:
        sys.stdout.write("спорных страниц нет\n")
        return 0

    marked = 0
    for sample in waiting[:LIMIT]:
        found = ask(sample)
        append(DONE_FILE, {"url": sample.get("url", ""), "at": int(time.time())})
        if found is None:
            continue
        append(OUT_FILE, {
            "host": sample.get("host", ""),
            "url": sample.get("url", ""),
            "title": sample.get("title", ""),
            "text": sample.get("text", ""),
            "label": int(bool(found.get("ai"))),
            "confidence": found.get("confidence", 0),
            "quote": str(found.get("quote", ""))[:300],
            "by": MODEL,
        })
        marked += 1

    sys.stdout.write("размечено страниц: %d\n" % marked)
    return 0


sys.exit(main())
