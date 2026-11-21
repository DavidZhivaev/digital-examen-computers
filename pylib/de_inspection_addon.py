import json
import re

from mitmproxy import http
from urllib.parse import parse_qs, urljoin, urlsplit

import de_common
import de_inspection as inspection


def approved(host, port):
    return port == 443 and inspection.host_name(host) in inspection.hosts.get()


def blocked():
    return http.Response.make(403, inspection.block_page(), {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
    })


MANAGEMENT_HOST = urlsplit(de_common.setting("DE_API_URL", "https://ex.1580.ru")).hostname
GROUP_CONFIG_PATH = re.compile(r"/api/computers/groups(?:/[0-9]+(?:/save)?)?/?$")


def scans_content(request, rules):
    return not (inspection.url_allowed(request.host, request.pretty_url, rules)
                or (inspection.host_name(request.host) == MANAGEMENT_HOST
                    and GROUP_CONFIG_PATH.fullmatch(urlsplit(request.path).path)))


AI_BLOCKED_CONNECTIONS = set()


def client_disconnected(client):
    AI_BLOCKED_CONNECTIONS.discard(client.id)


def http_connect(flow):
    if not approved(flow.request.host, flow.request.port):
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
    query = parse_qs(parts.query)
    if host.startswith("www."):
        host = host[4:]
    if host in {"ya.ru", "yandex.ru", "yandex.com", "alice.yandex.ru", "alice.yandex.com"}:
        return (host.startswith("alice.") or path == "/alice" or path.startswith("/alice/")
                or (path == "/search" and (bool(set(query.get("mode", ())) & {"ai", "alice", "neuro"})
                     or "force_neuro" in query.get("promo", ()))))
    if inspection.google_search_host(host):
        return path in {"/ai", "/aimode"} or (path == "/search" and "50" in query.get("udm", ()))
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
    if (not approved(request.host, request.port) or request.scheme != "https"
            or inspection.host_name(request.pretty_host) != inspection.host_name(request.host)):
        flow.response = blocked()
        return
    rules = inspection.groups.get()
    if not rules:
        return
    domain_blocked = inspection.url_blocked(request.host, request.pretty_url, rules)
    cached_ai_block = (flow.client_conn.id in AI_BLOCKED_CONNECTIONS
                       and not inspection.url_allowed(request.host, request.pretty_url, rules))
    if domain_blocked or cached_ai_block:
        flow.response = blocked()
        return
    if not scans_content(request, rules):
        return
    ai_enabled = any(rule.get("ai_auto") for rule in rules)
    if ai_enabled and ai_request(request):
        flow.response = blocked()
        return
    if inspection.content_terms(rules):
        if inspection.matches_content([request_text(request)], rules):
            flow.response = blocked()
            return
        if request.headers.get("sec-fetch-dest", "") not in (
                "style", "font", "image", "audio", "video", "script", "track"):
            request.anticomp()
    if ai_enabled and request.method == "GET":
        if inspection.yandex_search(request.host, request.path):
            flow.metadata["de_yandex_search"] = True
            request.anticomp()
            request.headers.pop("if-none-match", None)
            request.headers.pop("if-modified-since", None)
        path = inspection.google_web_search_path(request.host, request.path)
        if path != request.path:
            request.path = path


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
        if host and (inspection.url_blocked(host, target, rules)
                     or (not inspection.url_allowed(host, target, rules)
                         and any(rule.get("ai_auto") for rule in rules) and ai_url(target))):
            flow.response = blocked()
            return
    if not inspection.content_terms(rules):
        if not yandex_html(flow) or before.stream or not small_yandex_page(before):
            before.stream = True
            stream_yandex_style(flow)
        return
    scan_response(flow, body=False)
    if flow.response is not before:
        return
    limit = scan_limit(before)
    if not limit:
        before.stream = True
        return
    if before.stream and before.headers.get("content-encoding", "identity").lower() in ("", "identity"):
        header = response_text(before, b"")
        limit = inspection.JS_LIMIT if limit == inspection.JS_LIMIT else 3 * 65536
        before.stream = inspection.StreamPrefix(limit, lambda prefix: inspection.matches_content(
            [header + prefix], rules,
        ))
        stream_yandex_style(flow)


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


def stream_yandex_style(flow):
    if yandex_html(flow):
        add_yandex_style_headers(flow.response)
        flow.response.stream = inspection.YandexStyleStream(flow.response.stream)


def response(flow):
    before = flow.response
    if not before.stream and scans_content(flow.request, inspection.groups.get()):
        scan_response(flow, body=True)
        if flow.response is before and yandex_html(flow) and before.raw_content:
            add_yandex_style_headers(before)
            if len(before.raw_content) <= inspection.MAX_BODY:
                before.raw_content = inspection.filter_yandex_html(before.raw_content)
            else:
                before.raw_content = inspection.insert_yandex_style(before.raw_content)


def scan_response(flow, body):
    rules = inspection.groups.get()
    if not inspection.content_terms(rules):
        return
    reply = flow.response
    raw = response_text(reply, text_body(reply) if body else b"")
    if inspection.matches_content([raw], rules):
        flow.response = blocked()


def response_text(reply, body):
    first = f"{reply.http_version} {reply.status_code} {reply.reason}".encode()
    return inspection.message_text(first, reply.headers.fields, body)


def websocket_message(flow):
    rules = inspection.groups.get()
    message = flow.websocket.messages[-1]
    if (scans_content(flow.request, rules) and inspection.content_terms(rules) and message.is_text
            and inspection.matches_content([
        request_text(flow.request), message.content[:inspection.MAX_BODY],
    ], rules)):
        message.drop()
    del flow.websocket.messages[:-1]
