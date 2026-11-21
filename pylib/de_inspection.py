import base64
import binascii
import hashlib
import json
from pathlib import Path
import zlib
import html
from html.parser import HTMLParser
import re
from urllib.parse import parse_qs, parse_qsl, unquote_plus, urlencode, urlsplit

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
    b'.FuturisSearchWrapper,.serp-item__futuris-snippet,'
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
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.output = bytearray()
        self.hidden_tag = None
        self.depth = 0
        self.mask_token = False

    def is_ai_element(self, tag, attrs):
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if classes & {"FuturisSearchWrapper", "serp-item__futuris-snippet"}:
            return True
        return (tag == "a" and "HeaderNav-Tab" in classes
                and "promo=force_neuro" in (attributes.get("href") or ""))

    def handle_starttag(self, tag, attrs):
        if self.depth:
            if tag == self.hidden_tag:
                self.depth += 1
        elif self.is_ai_element(tag, attrs):
            if tag in {"area", "base", "br", "col", "embed", "hr", "img", "input",
                       "link", "meta", "param", "source", "track", "wbr"}:
                self.mask_token = True
            else:
                self.hidden_tag = tag
                self.depth = 1

    def handle_startendtag(self, tag, attrs):
        self.mask_token = self.is_ai_element(tag, attrs)

    def handle_endtag(self, tag):
        if self.depth and tag == self.hidden_tag:
            self.depth -= 1
            self.mask_token = True

    def updatepos(self, start, end):
        if end > start:
            if self.depth or self.mask_token:
                self.output.extend(b" " * (end - start))
            else:
                self.output.extend(self.rawdata[start:end].encode("latin-1"))
            self.mask_token = False
        return super().updatepos(start, end)

    def filter_chunk(self, data, final=False):
        self.feed(data.decode("latin-1"))
        if final:
            self.close()
            if self.rawdata:
                self.updatepos(0, len(self.rawdata))
                self.rawdata = ""
        output = bytes(self.output)
        self.output.clear()
        return output


def filter_yandex_html(body):
    return insert_yandex_style(YandexResultFilter().filter_chunk(body, final=True))


class YandexStyleStream:
    def __init__(self, inner):
        self.inner = inner
        self.pending = bytearray()
        self.done = False
        self.filter = YandexResultFilter()

    def __call__(self, chunk):
        data = self.inner(chunk) if callable(self.inner) else chunk
        data = self.filter.filter_chunk(data, final=not chunk)
        if self.done:
            return data
        self.pending.extend(data)
        if chunk and b">" not in self.pending and len(self.pending) < 512:
            return b""
        self.done = True
        return insert_yandex_style(bytes(self.pending)) if self.pending else b""
