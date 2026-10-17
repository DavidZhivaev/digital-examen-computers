#!/usr/bin/python3

import json
import os
import subprocess
import sys
import time

LLAMA = os.environ.get("DE_AI_LLAMA", "/usr/local/bin/llama-cli")
QWEN = os.environ.get("DE_AI_QWEN", "/models/qwen2.5-7b-instruct-q4_k_m.gguf")
THREADS = os.environ.get("DE_AI_THREADS", "4")
DOUBT_FILE = os.environ.get("DE_AI_DOUBT_FILE", "/models/doubt.jsonl")
OUT_FILE = os.environ.get("DE_AI_DATA", "/models/labelled.jsonl")
DONE_FILE = os.environ.get("DE_AI_DONE", "/models/doubt-done.jsonl")
LIMIT = int(os.environ.get("DE_AI_LABEL_LIMIT", "200"))

PROMPT = """Ты размечаешь страницы для школьного фильтра.
Ответь строго одной строкой JSON без пояснений:
{"ai": true|false, "confidence": 0..1, "quote": "короткая цитата со страницы"}
ai=true, если страница это разговор с искусственным интеллектом, ответ
нейросети или сервис генерации текста и картинок. ai=false во всех
остальных случаях, включая новости и статьи про ИИ.

Адрес: %s
Заголовок: %s
Текст: %s
"""


def ask(sample):
    text = PROMPT % (
        sample.get("url", "")[:300],
        sample.get("title", "")[:200],
        sample.get("text", "")[:1500],
    )
    command = [
        "nice", "-n", "19",
        LLAMA, "-m", QWEN, "-t", THREADS,
        "--temp", "0", "-n", "120", "--no-display-prompt", "-no-cnv",
        "-p", text,
    ]
    try:
        output = subprocess.check_output(command, stderr=subprocess.DEVNULL, timeout=600)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as error:
        sys.stdout.write("модель не ответила: %s\n" % error)
        return None

    body = output.decode("utf-8", "replace")
    start = body.find("{")
    end = body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        answer = json.loads(body[start:end + 1])
    except ValueError:
        return None
    if "ai" not in answer:
        return None
    return answer


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
    if not os.path.exists(QWEN) or not os.path.exists(LLAMA):
        sys.stdout.write("нет большой модели или llama-cli, разметка пропущена\n")
        return 0

    done = set()
    for row in read_jsonl(DONE_FILE):
        done.add(row.get("url", ""))

    waiting = [row for row in read_jsonl(DOUBT_FILE) if row.get("url", "") not in done]
    if not waiting:
        sys.stdout.write("спорных страниц нет\n")
        return 0

    marked = 0
    for sample in waiting[:LIMIT]:
        answer = ask(sample)
        append(DONE_FILE, {"url": sample.get("url", ""), "at": int(time.time())})
        if answer is None:
            continue
        append(OUT_FILE, {
            "host": sample.get("host", ""),
            "url": sample.get("url", ""),
            "title": sample.get("title", ""),
            "text": sample.get("text", ""),
            "label": int(bool(answer.get("ai"))),
            "confidence": answer.get("confidence", 0),
            "quote": str(answer.get("quote", ""))[:300],
            "by": "qwen2.5-7b",
        })
        marked += 1

    sys.stdout.write("размечено страниц: %d\n" % marked)
    return 0


sys.exit(main())
