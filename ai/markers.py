import os
import re

POLICY_DIR = os.environ.get("DE_AI_POLICY_DIR", "/etc/digital-examen/policy")

HOST_WEIGHT = 4.0

TEXT_MARKERS = (
    ("AI Overview", 2.0),
    ("ai_overview", 2.0),
    ("AI Mode", 1.0),
    ("Режим ИИ", 1.5),
    ("Нейро", 1.5),
    ("YandexGPT", 2.0),
    ("Алиса", 0.5),
    ("ChatGPT", 2.0),
    ("Gemini", 1.5),
    ("DeepSeek", 2.0),
    ("Copilot", 1.5),
    ("Отправить сообщение", 0.5),
    ("Спросите", 0.5),
    ("Ask anything", 1.0),
    ("Сгенерировать", 1.0),
    ("Сгенерировано ИИ", 2.5),
    ("нейросеть", 1.5),
    ("языковая модель", 2.0),
    ("prompt", 0.3),
    ("промпт", 1.0),
)

URL_MARKERS = (
    ("udm=50", 3.0),
    ("aim=", 1.0),
    ("/chat", 1.0),
    ("/conversation", 1.5),
    ("/completions", 2.5),
    ("/v1/messages", 2.5),
    ("/api/generate", 1.5),
    ("stream=true", 0.5),
)


def read_lines(name):
    path = os.path.join(POLICY_DIR, name)
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line.lower())
    return out


def host_hits(host, lines):
    host = (host or "").lower().rstrip(".")
    for pattern in lines:
        if pattern.startswith("."):
            if host == pattern[1:] or host.endswith(pattern):
                return True
        elif pattern.startswith("*."):
            tail = pattern[2:]
            if host == tail or host.endswith("." + tail):
                return True
        elif host == pattern:
            return True
    return False


def never_block(host):
    return host_hits(host, read_lines("never-block.txt"))


def known_ai_host(host):
    return host_hits(host, read_lines("ai-domains.txt"))


def sentence_around(text, word):
    place = text.lower().find(word.lower())
    if place < 0:
        return ""
    start = max(0, place - 90)
    end = min(len(text), place + 90)
    piece = re.sub(r"\s+", " ", text[start:end]).strip()
    return piece[:180]


def score(host, url, title, text):
    """Сколько признаков ИИ видно без модели и какая строка это показывает.

    Признаки с весами - то же решение, что в netpolicy на сайте: общие слова
    почти ничего не стоят, редкие и прямые стоят много. Модель получает эти
    же числа как добавку к тексту.
    """
    body = (title or "") + " " + (text or "")
    found = []
    total = 0.0

    if known_ai_host(host):
        total += HOST_WEIGHT
        found.append(("host", host, HOST_WEIGHT))

    low_url = (url or "").lower()
    for word, weight in URL_MARKERS:
        if word.lower() in low_url:
            total += weight
            found.append(("url", word, weight))

    for word, weight in TEXT_MARKERS:
        count = body.lower().count(word.lower())
        if count:
            add = weight * min(3, count)
            total += add
            found.append(("text", word, add))

    found.sort(key=lambda item: -item[2])
    quote = ""
    for kind, word, _ in found:
        if kind == "text":
            quote = sentence_around(body, word)
            if quote:
                break
    if not quote and found:
        quote = found[0][1]

    return total, found, quote


def vector(host, url, title, text):
    total, found, _ = score(host, url, title, text)
    kinds = {"host": 0.0, "url": 0.0, "text": 0.0}
    for kind, _, weight in found:
        kinds[kind] += weight
    return [total, kinds["host"], kinds["url"], kinds["text"], float(len(found))]
