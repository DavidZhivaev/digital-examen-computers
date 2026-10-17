import json
import os
import pickle
import re

import markers

MODEL_DIR = os.environ.get("DE_AI_MODEL_DIR", "/models")
MODEL_FILE = os.path.join(MODEL_DIR, "classifier.pkl")
EMBED_NAME = os.environ.get("DE_AI_EMBED", "intfloat/multilingual-e5-small")
EMBED_DIR = os.environ.get("DE_AI_EMBED_DIR", os.path.join(MODEL_DIR, "e5-small"))
TEXT_LIMIT = int(os.environ.get("DE_AI_TEXT_LIMIT", "1200"))

_embedder = [None]


def clean(text):
    text = re.sub(r"<script.*?</script>", " ", text or "", flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()[:TEXT_LIMIT]


def embedder():
    if _embedder[0] is None:
        from sentence_transformers import SentenceTransformer
        source = EMBED_DIR if os.path.isdir(EMBED_DIR) else EMBED_NAME
        _embedder[0] = SentenceTransformer(source, device="cpu")
    return _embedder[0]


def embed(texts):
    rows = ["query: " + item for item in texts]
    return embedder().encode(rows, normalize_embeddings=True, show_progress_bar=False)


def features(sample):
    text = clean(sample.get("text", ""))
    title = sample.get("title", "")
    host = sample.get("host", "")
    url = sample.get("url", "")
    line = " ".join([host, title, text])
    return line, markers.vector(host, url, title, text)


def build_rows(samples):
    lines = []
    extra = []
    for sample in samples:
        line, numbers = features(sample)
        lines.append(line)
        extra.append(numbers)

    vectors = embed(lines)
    out = []
    for index, row in enumerate(vectors):
        out.append(list(row) + extra[index])
    return out


def save(model, info):
    os.makedirs(MODEL_DIR, exist_ok=True)
    out = open(MODEL_FILE, "wb")
    pickle.dump({"model": model, "info": info}, out)
    out.close()
    report = open(os.path.join(MODEL_DIR, "classifier.json"), "w")
    report.write(json.dumps(info, ensure_ascii=False, indent=2))
    report.close()


def load():
    if not os.path.exists(MODEL_FILE):
        return None, {}
    data = pickle.load(open(MODEL_FILE, "rb"))
    return data.get("model"), data.get("info", {})
