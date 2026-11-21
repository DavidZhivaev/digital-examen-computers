import base64
import binascii
import json
from pathlib import Path
import zlib
from urllib.parse import parse_qs, unquote_plus, urlsplit

import de_patterns

HOSTS_FILE = Path("/etc/digital-examen/inspect-hosts")
POLICY_FILE = Path("/var/lib/digital-examen/policy.json")
LISTEN_PORT = 3130
MAX_BODY = 131072


def host_name(value):
    return value.lower().rstrip(".")


class CachedFile:
    def __init__(self, path, parse, empty):
        self.path = path
        self.parse = parse
        self.empty = empty
        self.stamp = None
        self.value = empty

    def get(self):
        try:
            stamp = self.path.stat().st_mtime_ns
            if stamp != self.stamp:
                value = self.parse(self.path.read_text())
                self.stamp, self.value = stamp, value
        except FileNotFoundError:
            self.stamp, self.value = None, self.empty
        except (OSError, ValueError):
            pass
        return self.value


class HostSet(frozenset):
    def __contains__(self, host):
        return super().__contains__("*") or super().__contains__(host)


def load_hosts(text):
    return HostSet(
        host_name(line.strip())
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


def load_groups(text):
    return json.loads(text).get("groups", [])


hosts = CachedFile(HOSTS_FILE, load_hosts, frozenset())
groups = CachedFile(POLICY_FILE, load_groups, [])


def matches_content(parts, rules):
    raw = b"\r\n\r\n".join(parts).decode("utf-8", "surrogateescape")
    combined = (raw + "\n" + unquote_plus(raw, errors="replace")).casefold()
    return any(term.casefold() in combined for rule in rules for term in rule.get("request_deny", []))


def message_text(first_line, headers, body):
    fields = b"\r\n".join(key + b": " + value for key, value in headers)
    return first_line + b"\r\n" + fields + b"\r\n\r\n" + (body or b"")[:MAX_BODY]



class StreamPrefix:
    def __init__(self, limit, denied):
        self.limit = limit
        self.denied = denied
        self.pending = bytearray()
        self.blocked = None

    def __call__(self, chunk):
        if self.blocked is not None:
            return b"" if self.blocked else chunk
        taken = min(self.limit - len(self.pending), len(chunk))
        self.pending.extend(chunk[:taken])
        if chunk and len(self.pending) < self.limit:
            return b""
        prefix = bytes(self.pending)
        self.pending.clear()
        self.blocked = self.denied(prefix)
        if self.blocked:
            return b""
        return prefix + chunk[taken:]


def block_page():
    for page in (Path("/etc/digital-examen/block.html"),
                 Path("/opt/digital-examen-computers/config/block.html"),
                 Path(__file__).resolve().parents[1] / "config/block.html"):
        try:
            return page.read_bytes()
        except OSError:
            continue
    return "<h1>Сайт закрыт ограничениями школы</h1>".encode()


def url_matches(patterns, target):
    def matches(pattern):
        offset = 0
        for part in pattern.split("*"):
            offset = target.find(part, offset)
            if offset < 0:
                return False
            offset += len(part)
        return True

    patterns = patterns or []
    return (any(matches(pattern) for pattern in patterns if not pattern.startswith("!"))
            and not any(matches(pattern[1:]) for pattern in patterns if pattern.startswith("!")))


def search_return_url(host, target):
    if host not in {"yandex.ru", "www.yandex.ru", "ya.ru", "www.ya.ru"}:
        return ""
    try:
        challenge = urlsplit(target)
        if challenge.path.rstrip("/") != "/showcaptcha":
            return ""
        value = parse_qs(challenge.query).get("retpath", [""])[0]
        token, separator, signature = value.rpartition("_")
        if not separator or len(signature) != 32 or any(ch not in "0123456789abcdef" for ch in signature):
            token = value
        if not token or len(token) > 4096:
            return ""
        original = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True).decode()
        search = urlsplit(original)
        if (search.scheme in ("http", "https") and search.hostname == host
                and search.path.rstrip("/") == "/search"):
            return original
    except (ValueError, UnicodeError, binascii.Error):
        pass
    return ""


def url_blocked(host, target, rules):
    if any(de_patterns.any_match(rule.get("always_allow"), host)
           or url_matches(rule.get("url_allow"), target) for rule in rules):
        return False
    if any(de_patterns.any_match(rule.get("denied"), host)
           or url_matches(rule.get("url_denied"), target) for rule in rules):
        return True
    redirected = search_return_url(host, target)
    if redirected and any(url_matches(rule.get("url_allow"), redirected) for rule in rules):
        return any(url_matches(rule.get("url_denied"), redirected) for rule in rules)
    return any(rule.get("principle") == "allow" for rule in rules)


def text_prefix(data, encoding):
    if not data:
        return b""
    if encoding in ("", "identity"):
        return data[:MAX_BODY]
    if encoding in ("gzip", "deflate"):
        try:
            bits = 31 if encoding == "gzip" else 15
            return zlib.decompressobj(bits).decompress(data[:MAX_BODY], MAX_BODY)
        except zlib.error:
            return b""
    return b""
