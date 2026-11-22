import base64
import binascii
import hashlib
import json
from pathlib import Path
import zlib
import html
from html.parser import HTMLParser
import re
import time
from urllib.parse import parse_qs, parse_qsl, unquote_plus, urlencode, urlsplit

import de_patterns

POLICY_FILE = Path("/var/lib/digital-examen/policy.json")
LISTEN_PORT = 3130
ORIGIN_PORT = 3140
BLOCK_PAGE_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'none'; base-uri 'none'"
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


def load_groups(text):
    return json.loads(text).get("groups", [])


groups = CachedFile(POLICY_FILE, load_groups, [])


def content_terms(rules):
    return tuple(term.casefold() for rule in rules for term in rule.get("request_deny", []) if term)


def matches_content(parts, rules):
    terms = content_terms(rules)
    if not terms:
        return False
    raw = b"\r\n\r\n".join(parts).decode("utf-8", "surrogateescape")
    folded = raw.casefold()
    if any(term in folded for term in terms):
        return True
    decoded = html.unescape(unquote_plus(raw, errors="replace"))
    decoded = re.sub(r"\\u([0-9a-fA-F]{4})", lambda match: chr(int(match[1], 16)), decoded)
    visible = re.sub(r"</?[A-Za-z][^<>]{0,512}>", " ", decoded)
    normalized = " ".join(visible.casefold().split())
    decoded = decoded.casefold()
    return any(term in decoded or term in normalized for term in terms)


def matching_terms(parts, terms):
    if not terms:
        return ()
    raw = b"\r\n\r\n".join(parts).decode("utf-8", "surrogateescape")
    folded = raw.casefold()
    decoded = html.unescape(unquote_plus(raw, errors="replace"))
    decoded = re.sub(r"\\u([0-9a-fA-F]{4})", lambda match: chr(int(match[1], 16)), decoded)
    visible = re.sub(r"</?[A-Za-z][^<>]{0,512}>", " ", decoded)
    normalized = " ".join(visible.casefold().split())
    decoded = decoded.casefold()
    return tuple(term for term in terms if term in folded or term in decoded or term in normalized)


def message_text(first_line, headers, body):
    fields = b"\r\n".join(key + b": " + value for key, value in headers)
    return first_line + b"\r\n" + fields + b"\r\n\r\n" + (body or b"")[:MAX_BODY]



class StreamPrefix:
    def __init__(self, limit, denied, defer=None, deadline=None):
        self.defer = defer
        self.deadline = deadline
        self.released = False
        self.limit = limit
        self.denied = denied
        self.pending = bytearray()
        self.blocked = None

    def __call__(self, chunk):
        if self.blocked is not None:
            return b"" if self.blocked else chunk
        previous = len(self.pending)
        taken = min(self.limit - previous, len(chunk))
        self.pending.extend(chunk[:taken])
        complete = not chunk or len(self.pending) == self.limit
        late = self.deadline is not None and time.monotonic() >= self.deadline
        if not complete and not late:
            return b""
        prefix = bytes(self.pending)
        output = chunk if self.released else prefix[:previous] + chunk
        if late:
            self.released = True
        if complete:
            self.pending.clear()
            if self.released:
                self.defer(prefix)
                self.blocked = False
            else:
                self.blocked = self.denied(prefix)
        return b"" if self.blocked else output


def stream_chunks(transform):
    def chunks(chunk):
        data = transform(chunk)
        return [data] if data else []
    return chunks


def block_page():
    for page in (Path("/etc/digital-examen/block.html"),
                 Path("/opt/digital-examen-computers/config/block.html"),
                 Path(__file__).resolve().parents[1] / "config/block.html"):
        try:
            return page.read_bytes()
        except OSError:
            continue
    return "<h1>Сайт закрыт ограничениями школы</h1>".encode()


URL_WITH_HOST = re.compile(r"(?:(https?)://)?((?:\*\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+)(:\d+)?(?=[/?#]|$)", re.I)


def url_matches(patterns, target):
    def matches(pattern):
        value = target
        address = URL_WITH_HOST.match(pattern)
        if address:
            scheme, host, port = address.groups()
            try:
                url = urlsplit(target)
                target_port = url.port or (443 if url.scheme == "https" else 80)
            except ValueError:
                return False
            if (not de_patterns.host_matches(host, url.hostname or "")
                    or scheme and scheme.lower() != url.scheme
                    or port and int(port[1:]) != target_port):
                return False
            value = url.path + ("?" + url.query if url.query else "")
            pattern = pattern[address.end():]
            if not value.startswith(pattern.split("*", 1)[0]):
                return False
        offset = 0
        for part in pattern.split("*"):
            offset = value.find(part, offset)
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


def url_allowed(host, target, rules):
    return any(de_patterns.any_match(rule.get("always_allow"), host)
               or url_matches(rule.get("url_allow"), target) for rule in rules)


def url_blocked(host, target, rules):
    if url_allowed(host, target, rules):
        return False
    if any(de_patterns.any_match(rule.get("denied"), host)
           or url_matches(rule.get("url_denied"), target) for rule in rules):
        return True
    redirected = search_return_url(host, target)
    if redirected and any(url_matches(rule.get("url_allow"), redirected) for rule in rules):
        return any(url_matches(rule.get("url_denied"), redirected) for rule in rules)
    return any(rule.get("principle") == "allow" for rule in rules)


JS_LIMIT = 32 * 1024
JS_TYPES = frozenset({"application/javascript", "text/javascript", "application/ecmascript",
                      "text/ecmascript", "application/x-javascript"})


def body_scan_limit(content_type, length=None):
    mime = content_type.partition(";")[0].strip().lower()
    if (mime in {"text/html", "text/plain", "application/json", "text/json",
                 "application/x-ndjson", "application/xml", "text/xml"}
            or mime.endswith(("+json", "+xml")) and not mime.startswith("image/")):
        return MAX_BODY
    if mime in JS_TYPES and length is not None:
        try:
            if 0 < int(length) <= JS_LIMIT:
                return JS_LIMIT
        except (TypeError, ValueError):
            pass
    return 0


def text_prefix(data, encoding, limit=MAX_BODY):
    if not data or not limit:
        return b""
    javascript = limit == JS_LIMIT
    if encoding in ("", "identity"):
        return b"" if javascript and len(data) > limit else data[:limit]
    if encoding in ("gzip", "deflate"):
        try:
            bits = 31 if encoding == "gzip" else 15
            decoded = zlib.decompressobj(bits).decompress(data[:MAX_BODY], limit + int(javascript))
            return b"" if javascript and len(decoded) > limit else decoded
        except zlib.error:
            return b""
    return b""


def google_search_host(host):
    host = host_name(host)
    if host.startswith("www."):
        host = host[4:]
    return bool(re.fullmatch(r"google\.(?:com|[a-z]{2}|(?:com|co)\.[a-z]{2})", host))


def google_web_search_path(host, path):
    if not google_search_host(host):
        return path
    parts = urlsplit(path)
    if parts.path.rstrip("/") != "/search":
        return path
    query = parse_qsl(parts.query, keep_blank_values=True)
    params = dict(query)
    if not params.get("q") or params.get("tbm") or any(
            key == "udm" and value not in ("", "0") for key, value in query):
        return path
    query = [(key, value) for key, value in query if key != "udm"]
    return parts._replace(query=urlencode([*query, ("udm", "14")])).geturl()


YANDEX_SEARCH_HOSTS = frozenset({"ya.ru", "www.ya.ru", "yandex.ru", "www.yandex.ru",
                                "yandex.com", "www.yandex.com"})
YANDEX_SEARCH_STYLE = (
    b'li[class*="__futuris-snippet"],'
    b'a.HeaderNav-Tab[href*="promo=force_neuro"]{display:none!important}'
)
YANDEX_STYLE_TAG = b"<style>" + YANDEX_SEARCH_STYLE + b"</style>"
YANDEX_STYLE_HASH = "'sha256-" + base64.b64encode(hashlib.sha256(YANDEX_SEARCH_STYLE).digest()).decode() + "'"


def yandex_search(host, path):
    return host_name(host) in YANDEX_SEARCH_HOSTS and urlsplit(path).path.rstrip("/") == "/search"


def yandex_style_policy(policy):
    directives = [item.strip().split() for item in policy.split(";") if item.strip()]
    for name in ("style-src-elem", "style-src", "default-src"):
        tokens = next((item for item in directives if item[0].lower() == name), None)
        if tokens is None:
            continue
        if YANDEX_STYLE_HASH in tokens[1:]:
            return policy
        if ("'unsafe-inline'" in tokens[1:]
                and not any(token.startswith(("'nonce-", "'sha256-", "'sha384-", "'sha512-"))
                            for token in tokens[1:])):
            return policy
        element_style = ["style-src-elem", *(token for token in tokens[1:] if token != "'none'"),
                         YANDEX_STYLE_HASH]
        if name == "style-src-elem":
            tokens[:] = element_style
        else:
            directives.append(element_style)
        return "; ".join(" ".join(item) for item in directives)
    return policy


def insert_yandex_style(html_body):
    head = re.search(br"<head(?:\s[^<>]*)?>", html_body[:4096], re.I)
    doctype = re.match(br"\s*<!doctype[^>]*>", html_body[:4096], re.I)
    index = head.end() if head else doctype.end() if doctype else 0
    return html_body[:index] + YANDEX_STYLE_TAG + html_body[index:]


class YandexResultFilter(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=False)
        self.source = source.decode("latin-1")
        self.lines = [0, *(match.end() for match in re.finditer("\n", self.source))]
        self.target = None
        self.depth = 0
        self.ranges = []

    def source_offset(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if self.target is not None:
            if tag == self.target[0]:
                self.depth += 1
            return
        if tag not in {"a", "li"}:
            return
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        snippet = tag == "li" and any("futuris-snippet" in name for name in classes)
        tab = (tag == "a" and "HeaderNav-Tab" in classes
               and "promo=force_neuro" in (attributes.get("href") or ""))
        if snippet or tab:
            self.target = (tag, self.source_offset())
            self.depth = 1

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if self.target is None or tag != self.target[0]:
            return
        self.depth -= 1
        if not self.depth:
            start = self.target[1]
            end = self.source.find(">", self.source_offset()) + 1
            if 0 < end - start <= JS_LIMIT:
                self.ranges.append((start, end))
            self.target = None


def filter_yandex_html(body):
    parser = YandexResultFilter(body)
    try:
        parser.feed(parser.source)
        parser.close()
    except (AssertionError, ValueError):
        return insert_yandex_style(body)
    output = bytearray(body)
    for start, end in parser.ranges:
        output[start:end] = b" " * (end - start)
    return insert_yandex_style(bytes(output))


class YandexStyleStream:
    def __init__(self, inner):
        self.inner = inner
        self.pending = bytearray()
        self.done = False

    def __call__(self, chunk):
        data = self.inner(chunk) if callable(self.inner) else chunk
        if self.done:
            return data
        self.pending.extend(data)
        if chunk and b">" not in self.pending and len(self.pending) < 512:
            return b""
        self.done = True
        return insert_yandex_style(bytes(self.pending)) if self.pending else b""
