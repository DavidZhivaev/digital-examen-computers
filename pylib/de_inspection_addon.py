import asyncio
from concurrent.futures import ThreadPoolExecutor
import html
import json
import re
import zlib

from mitmproxy import http
from urllib.parse import parse_qs, urljoin, urlsplit

import de_common
import de_block_history
import de_learned
import de_inspection as inspection


def blocked():
    return http.Response.make(403, inspection.block_page(), {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
        "Content-Security-Policy": inspection.BLOCK_PAGE_CSP,
    })


def deny(flow, reason, group="", trigger=""):
    de_block_history.remember(flow.request.pretty_url, reason, group, trigger)
    flow.response = blocked()


def deny_phrase(flow, parts, rules):
    terms = inspection.matching_terms(parts, inspection.content_terms(rules))
    deny(flow, *inspection.phrase_denial(terms, rules))


def block_response_headers(flow, denial):
    de_block_history.remember(flow.request.pretty_url, *denial)
    response = blocked()
    chunks = iter(([response.raw_content],) if flow.request.method != "HEAD" else ())
    response.stream = lambda _: next(chunks, [])
    flow.response = response


MANAGEMENT_HOST = urlsplit(de_common.setting("DE_API_URL", "https://ex.1580.ru")).hostname
WIKI_AI_TOPIC = re.compile(
    r"искусственн\w+ интеллект\w*|artificial intelligence|machine learning|машинн\w+ обучени\w*"
    r"|нейронн\w+ сет\w*|neural networks?|chatgpt|large language models?|больш\w+ языков\w+ модел\w*",
    re.IGNORECASE,
)
GAMES_TITLE = re.compile(
    r"играть онлайн|онлайн[- ]игры|игры онлайн|бесплатные игры"
    r"|(?:play|free) (?:free )?online games|play games online",
    re.IGNORECASE,
)
VIDEO_GAME_MARKUP = re.compile(br'"@type"\s*:\s*"VideoGame"', re.IGNORECASE)
HTML_TITLE = re.compile(br"<title\b[^>]*>([^<]{1,4096})</title\s*>", re.IGNORECASE)
GROUP_CONFIG_PATH = re.compile(r"/api/computers/groups(?:/[0-9]+(?:/save)?)?/?$")


def scans_content(request, rules):
    return not (inspection.url_allowed(request.host, request.pretty_url, rules)
                or (inspection.host_name(request.host) == MANAGEMENT_HOST
                    and GROUP_CONFIG_PATH.fullmatch(urlsplit(request.path).path)))


AI_BLOCKED_CONNECTIONS = set()
SCAN_TIMEOUT = 0.3
SCAN_WORKERS = ThreadPoolExecutor(max_workers=2)
SCAN_PENDING = set()


def client_disconnected(client):
    AI_BLOCKED_CONNECTIONS.discard(client.id)


def http_connect(flow):
    if flow.request.port != 443:
        flow.response = blocked()
    elif flow.request.headers.get("X-Digital-Examen-Block") == "ai":
        AI_BLOCKED_CONNECTIONS.add(flow.client_conn.id)


def request_text(request):
    first = f"{request.method} {request.path} {request.http_version}".encode()
    fields = request.headers.fields
    if "host" not in request.headers:
        authority = request.host_header or request.authority or request.host
        fields = ((b"Host", authority.encode("ascii", "backslashreplace")), *fields)
    return inspection.message_text(first, fields, text_body(request))


def scan_limit(message):
    length = message.headers.get("content-length")
    if length is None and message.raw_content is not None:
        length = len(message.raw_content)
    return inspection.body_scan_limit(message.headers.get("content-type", ""), length)


def text_body(message):
    return inspection.text_prefix(
        message.raw_content, message.headers.get("content-encoding", "identity").lower(), scan_limit(message)
    )


def ai_url(url):
    parts = urlsplit(url)
    host = inspection.host_name(parts.hostname or "")
    path = parts.path.lower().rstrip("/")
    if host.startswith("www."):
        host = host[4:]
    if host in {"ya.ru", "yandex.ru", "yandex.com", "alice.yandex.ru", "alice.yandex.com"}:
        if host.startswith("alice.") or path == "/alice" or path.startswith("/alice/"):
            return True
        if path != "/search":
            return False
        query = parse_qs(parts.query)
        return bool(set(query.get("mode", ())) & {"ai", "alice", "neuro"}) or "force_neuro" in query.get("promo", ())
    if inspection.google_search_host(host):
        return path in {"/ai", "/aimode"} or (path == "/search" and "50" in parse_qs(parts.query).get("udm", ()))
    return host in {"gemini.google.com", "aistudio.google.com"}


def ai_request(request):
    if ai_url(request.pretty_url):
        return True
    if request.method != "POST" or "application/json" not in request.headers.get("content-type", "").lower():
        return False
    path = request.path.split("?", 1)[0]
    if path not in ("/v1/chat/completions", "/v1/responses", "/v1/messages", "/api/chat", "/api/generate") and not re.fullmatch(
        r"/v1(?:beta)?/models/[^/]+:(?:streamGenerateContent|generateContent)", path,
    ):
        return False
    body = text_body(request)
    if not body or len(body) > inspection.MAX_BODY:
        return False
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        return False
    if not isinstance(data, dict):
        return False
    if path.startswith("/v1") and "/models/" in path:
        contents = data.get("contents")
        return isinstance(contents, list) and any(
            isinstance(item, dict) and isinstance(item.get("parts"), list) for item in contents
        )
    if not isinstance(data.get("model"), str):
        return False
    if path == "/v1/responses":
        return isinstance(data.get("input"), (str, list))
    if path == "/api/generate":
        return isinstance(data.get("prompt"), str) and any(
            field in data for field in ("stream", "options", "keep_alive", "system")
        )
    messages = data.get("messages")
    return isinstance(messages, list) and any(
        isinstance(item, dict) and item.get("role") in ("user", "assistant", "system") and "content" in item
        for item in messages
    )


def request(flow):
    request = flow.request
    if (request.port != 443 or request.scheme != "https"
            or inspection.host_name(request.pretty_host) != inspection.host_name(request.host)):
        flow.response = blocked()
        return
    rules = inspection.groups.get()
    if not rules or inspection.host_allowed(request.host, rules):
        return
    domain_blocked = inspection.url_denial(request.host, request.pretty_url, rules)
    ai_enabled = any(rule.get("ai_auto") for rule in rules)
    cached_ai_block = (ai_enabled and flow.client_conn.id in AI_BLOCKED_CONNECTIONS
                       and not inspection.url_allowed(request.host, request.pretty_url, rules))
    if domain_blocked:
        deny(flow, *domain_blocked)
        return
    if cached_ai_block:
        deny(flow, "Классификация домена", next((rule.get("name", "") for rule in rules if rule.get("ai_auto")), ""), request.host)
        return
    if inspection.ad_request(request.host, request.headers.get("sec-fetch-dest", "")):
        de_block_history.remember(request.pretty_url, "Реклама", "Блокировщик рекламы", request.host)
        flow.response = http.Response.make(204, b"", {"Cache-Control": "no-store"})
        return
    host = inspection.host_name(request.host)
    if (host in inspection.YANDEX_SEARCH_HOSTS or inspection.google_search_host(host)
            or host in {"suggest.yandex.ru", "suggest.yandex.net", "suggest.yandex.com"}):
        query = urlsplit(request.path).query
        if query and inspection.matches_content([query.encode()], rules):
            deny_phrase(flow, [query.encode()], rules)
            return
    if not scans_content(request, rules):
        return
    if request.method == "GET":
        known = de_learned.blocked_terms(de_learned.endpoint(request.pretty_url), inspection.content_terms(rules))
        if known:
            deny(flow, *inspection.phrase_denial(known, rules))
            return
    if ai_enabled and ai_request(request):
        deny(flow, "Запрос к ИИ", next((rule.get("name", "") for rule in rules if rule.get("ai_auto")), ""), request.host)
        return
    if inspection.content_terms(rules):
        raw = request_text(request)
        if inspection.matches_content([raw], rules):
            deny_phrase(flow, [raw], rules)
            return
        if request.headers.get("sec-fetch-dest", "") not in (
                "style", "font", "image", "audio", "video", "script", "track"):
            encodings = {entry.strip().lower() for entry in request.headers.get("accept-encoding", "").split(",")}
            supported = [codec for codec in ("gzip", "deflate") if codec in encodings]
            if supported:
                request.headers["accept-encoding"] = ", ".join(supported)
            else:
                request.anticomp()
    if ai_enabled and request.method == "GET":
        if inspection.yandex_search(request.host, request.path):
            flow.metadata["de_yandex_search"] = True
            if not inspection.content_terms(rules):
                request.anticomp()
            request.headers.pop("if-none-match", None)
            request.headers.pop("if-modified-since", None)
        path = inspection.google_web_search_path(request.host, request.path)
        if path != request.path:
            request.path = path


def page_topics(flow, rules):
    if flow.response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "text/html":
        return False, False
    host = inspection.host_name(flow.request.host)
    wiki_ai = ((host == "wikipedia.org" or host.endswith(".wikipedia.org"))
               and any(rule.get("ai_auto") for rule in rules))
    games = any(rule.get("name", "").strip().casefold() == "игры" for rule in rules)
    return wiki_ai, games


def topic_denied(flow, rules):
    wiki_ai, games = page_topics(flow, rules)
    if not (wiki_ai or games):
        return False
    body = text_body(flow.response)
    match = HTML_TITLE.search(body)
    title = html.unescape(match.group(1).decode("utf-8", "replace")) if match else ""
    if wiki_ai and WIKI_AI_TOPIC.search(title):
        return "Классификация страницы", next(rule.get("name", "") for rule in rules if rule.get("ai_auto")), title
    if games and (GAMES_TITLE.search(title) or VIDEO_GAME_MARKUP.search(body)):
        return "Классификация страницы", "Игры", title
    return None


def responseheaders(flow):
    before = flow.response
    rules = inspection.groups.get()
    if not scans_content(flow.request, rules):
        before.stream = True
        return
    location = before.headers.get("location", "")
    if location and 300 <= before.status_code < 400:
        target = urljoin(flow.request.pretty_url, location)
        host = urlsplit(target).hostname
        if host:
            denial = inspection.url_denial(host, target, rules)
            if denial is None and not inspection.url_allowed(host, target, rules) and ai_url(target):
                group = next((rule for rule in rules if rule.get("ai_auto")), None)
                if group is not None:
                    denial = ("Переадресация на ИИ", group.get("name", ""), host)
            if denial:
                block_response_headers(flow, denial)
                return
    if not inspection.content_terms(rules) and not any(page_topics(flow, rules)):
        if not yandex_html(flow) or before.stream or not small_yandex_page(before):
            before.stream = True
            stream_yandex_style(flow)
        return
    raw = response_text(before, b"")
    if inspection.matches_content([raw], rules):
        block_response_headers(flow, inspection.phrase_denial(
            inspection.matching_terms([raw], inspection.content_terms(rules)), rules))
        return
    if (flow.request.method == "HEAD" or before.status_code < 200
            or before.status_code in (204, 304) or not scan_limit(before)):
        before.stream = True
        return
    before.stream = False


def yandex_html(flow):
    reply = flow.response
    return (flow.metadata.get("de_yandex_search") and reply.status_code == 200
            and reply.headers.get("content-type", "").lower().split(";", 1)[0].strip() == "text/html"
            and reply.headers.get("content-encoding", "identity").lower() in ("", "identity")
            and reply.headers.get("content-length") != "0")


def small_yandex_page(reply):
    length = reply.headers.get("content-length", "")
    return length.isdecimal() and 0 < int(length) <= inspection.MAX_BODY


def add_yandex_style_headers(reply):
    key = "content-security-policy"
    values = reply.headers.get_all(key)
    if values:
        reply.headers.set_all(key, [", ".join(inspection.yandex_style_policy(policy)
                                               for policy in value.split(",")) for value in values])
    length = reply.headers.get("content-length")
    if length and length.isdecimal():
        reply.headers["content-length"] = str(int(length) + len(inspection.YANDEX_STYLE_TAG))
    reply.headers["cache-control"] = "no-store"
    for key in ("etag", "content-md5", "digest"):
        reply.headers.pop(key, None)


def stream_yandex_style(flow, inner=True):
    if yandex_html(flow):
        add_yandex_style_headers(flow.response)
        flow.response.stream = inspection.stream_chunks(inspection.YandexStyleStream(inner))


async def response(flow):
    before = flow.response
    rules = inspection.groups.get()
    if not before.stream and scans_content(flow.request, rules):
        if before.raw_content and scan_limit(before):
            denial = topic_denied(flow, rules)
            if denial:
                deny(flow, *denial)
                return
            if inspection.content_terms(rules):
                await scan_buffered_response(flow, rules)
        if (flow.response is before and flow.metadata.get("de_yandex_search") and before.raw_content
                and before.status_code == 200
                and before.headers.get("content-type", "").split(";", 1)[0].strip().lower() == "text/html"):
            data = before.raw_content
            encoding = before.headers.get("content-encoding", "identity").lower()
            if encoding in ("gzip", "deflate"):
                try:
                    decoder = zlib.decompressobj(31 if encoding == "gzip" else 15)
                    data = decoder.decompress(data, 8 * 1024 * 1024)
                    if not decoder.eof:
                        return
                except zlib.error:
                    return
                before.headers.pop("content-encoding", None)
            elif encoding not in ("", "identity"):
                return
            add_yandex_style_headers(before)
            if len(data) <= inspection.MAX_BODY:
                before.raw_content = inspection.filter_yandex_html(data)
            else:
                before.raw_content = inspection.insert_yandex_style(data)
            if "content-length" in before.headers:
                before.headers["content-length"] = str(len(before.raw_content))


def enqueue_scan(url, scan, *args):
    if len(SCAN_PENDING) >= 8:
        return None
    future = asyncio.get_running_loop().run_in_executor(SCAN_WORKERS, scan, *args)
    SCAN_PENDING.add(future)

    def remember(result):
        SCAN_PENDING.discard(result)
        if not result.cancelled() and result.exception() is None and result.result():
            try:
                de_learned.remember(url, result.result())
            except OSError:
                pass

    future.add_done_callback(remember)
    return future


async def scan_buffered_response(flow, rules):
    raw = response_text(flow.response, text_body(flow.response))
    url = de_learned.endpoint(flow.request.pretty_url) if flow.request.method == "GET" else ""
    future = enqueue_scan(url, inspection.matching_terms, [raw], inspection.content_terms(rules))
    if future is None:
        deny(flow, "Очередь проверки заполнена")
        return
    done, _ = await asyncio.wait([future], timeout=SCAN_TIMEOUT)
    if not done:
        deny(flow, "Истекло время проверки")
    elif future.result():
        deny(flow, *inspection.phrase_denial(future.result(), rules))


def response_text(reply, body):
    first = f"{reply.http_version} {reply.status_code} {reply.reason}".encode()
    return inspection.message_text(first, reply.headers.fields, body)


def websocket_message(flow):
    rules = inspection.groups.get()
    message = flow.websocket.messages[-1]
    terms = inspection.content_terms(rules)
    if terms and scans_content(flow.request, rules):
        if not message.is_text or len(message.content) > inspection.MAX_BODY:
            de_block_history.remember(flow.request.pretty_url, "Бинарный или слишком большой WebSocket")
            message.drop()
        else:
            parts = [request_text(flow.request), message.content]
            if inspection.matches_content(parts, rules):
                denied = inspection.matching_terms(parts, terms)
                de_block_history.remember(flow.request.pretty_url, *inspection.phrase_denial(denied, rules))
                message.drop()
    del flow.websocket.messages[:-1]
