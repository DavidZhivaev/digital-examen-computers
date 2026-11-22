import json
from pathlib import Path
from tempfile import NamedTemporaryFile
import time
from urllib.parse import urlsplit

from de_inspection import CachedFile, google_search_host, yandex_search

STATE = Path("/var/lib/digital-examen")
LOCAL = STATE / "learned-local.json"
SHARED = STATE / "learned-shared.json"
local = CachedFile(LOCAL, json.loads, {})
shared = CachedFile(SHARED, json.loads, {})


def endpoint(url):
    parts = urlsplit(url)
    host = parts.hostname
    if not host or parts.scheme not in ("http", "https"):
        return ""
    path = parts.path or "/"
    if path.rstrip("/") == "/search" and (google_search_host(host) or yandex_search(host, path + "?text=q")):
        return ""
    key = parts.scheme + "://" + parts.netloc.lower() + path
    return key if len(key) <= 2048 else ""


def write(path, value):
    with NamedTemporaryFile(mode="w", dir=path.parent, prefix=".learned-", delete=False) as output:
        temporary = Path(output.name)
        try:
            json.dump(value, output, ensure_ascii=True)
            output.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def active():
    now = int(time.time())
    return {url: {term: expiry for term, expiry in terms.items() if expiry > now}
            for url, terms in local.get().items() if any(expiry > now for expiry in terms.values())}


def remember(url, terms):
    if not url or not terms:
        return
    rows = active()
    existing = rows.pop(url, {})
    rows[url] = {**existing, **dict.fromkeys(terms, int(time.time()) + 86400)}
    write(LOCAL, dict(list(rows.items())[-2048:]))


def blocked(url, terms):
    if not url or not terms:
        return False
    known = local.get().get(url, {})
    now = int(time.time())
    return any(known.get(term, 0) > now or term in shared.get().get(url, ()) for term in terms)
