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


def scans_content(request):
    return not (request.host.lower().rstrip(".") == MANAGEMENT_HOST
                and GROUP_CONFIG_PATH.fullmatch(urlsplit(request.path).path))


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


def is_text_message(message):
    content_type = message.headers.get("content-type", "").lower()
    return content_type.startswith("text/") or any(
        kind in content_type for kind in ("json", "xml", "javascript", "x-www-form-urlencoded")
    )


def text_body(message):
    return inspection.text_prefix(
        message.raw_content, message.headers.get("content-encoding", "identity").lower()
    ) if is_text_message(message) else b""


def ai_url(url):
    parts = urlsplit(url)
    host = inspection.host_name(parts.hostname or "")
    path = parts.path.lower().rstrip("/")
    query = parse_qs(parts.query)
    if host.startswith("www."):
        host = host[4:]
    if host in {"ya.ru", "yandex.ru", "yandex.com", "alice.yandex.ru", "alice.yandex.com"}:
        return (host.startswith("alice.") or path == "/alice" or path.startswith("/alice/")
                or (path == "/search" and bool(set(query.get("mode", ())) & {"ai", "alice", "neuro"})))
    if re.fullmatch(r"google\.(?:com|[a-z]{2}|(?:com|co)\.[a-z]{2})", host):
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
    domain_blocked = (flow.client_conn.id in AI_BLOCKED_CONNECTIONS
                      or inspection.url_blocked(request.host, request.pretty_url, rules))
    if domain_blocked or (scans_content(request) and (
            (any(rule.get("ai_auto") for rule in rules) and ai_request(request))
            or (inspection.content_terms(rules) and inspection.matches_content([request_text(request)], rules)))):
        flow.response = blocked()


def responseheaders(flow):
    if not scans_content(flow.request):
        return
    before = flow.response
    rules = inspection.groups.get()
    location = before.headers.get("location", "")
    if location and 300 <= before.status_code < 400:
        target = urljoin(flow.request.pretty_url, location)
        host = urlsplit(target).hostname
        if host and (inspection.url_blocked(host, target, rules)
                     or (any(rule.get("ai_auto") for rule in rules) and ai_url(target))):
            flow.response = blocked()
            return
    if not inspection.content_terms(rules):
        return
    scan_response(flow, body=False)
    if (flow.response is not before or not before.stream or not is_text_message(before)
            or before.headers.get("content-encoding", "identity").lower() not in ("", "identity")):
        return
    request = request_text(flow.request)
    header = response_text(before, b"")
    before.stream = inspection.StreamPrefix(3 * 65536, lambda prefix: inspection.matches_content(
        [request, header + prefix], rules,
    ))


def response(flow):
    if scans_content(flow.request) and not flow.response.stream:
        scan_response(flow, body=True)


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
    if inspection.content_terms(rules) and message.is_text and inspection.matches_content([
        request_text(flow.request), message.content[:inspection.MAX_BODY],
    ], rules):
        message.drop()
    del flow.websocket.messages[:-1]
