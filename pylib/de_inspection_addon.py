import json
import re

from mitmproxy import http

import de_inspection as inspection
import de_patterns


def approved(host, port):
    return port == 443 and inspection.host_name(host) in inspection.hosts.get()


def blocked():
    return http.Response.make(403, "Запрос запрещён политикой школы".encode(), {
        "Content-Type": "text/plain; charset=utf-8",
        "Cache-Control": "no-store",
    })


def active_rules(flow):
    rules = inspection.groups.get()
    if any(de_patterns.any_match(rule.get("always_allow"), flow.request.host) for rule in rules):
        return []
    return rules


def http_connect(flow):
    if not approved(flow.request.host, flow.request.port):
        flow.response = blocked()


def request_text(request):
    first = f"{request.method} {request.path} {request.http_version}".encode()
    fields = request.headers.fields
    if "host" not in request.headers:
        authority = request.host_header or request.authority or request.host
        fields = ((b"Host", authority.encode("ascii", "backslashreplace")), *fields)
    return inspection.message_text(first, fields, text_body(request))


def is_text_message(message):
    if message.headers.get("content-encoding", "identity").lower() not in ("", "identity"):
        return False
    content_type = message.headers.get("content-type", "").lower()
    return content_type.startswith("text/") or any(
        kind in content_type for kind in ("json", "xml", "javascript", "x-www-form-urlencoded")
    )


def text_body(message):
    return message.raw_content if is_text_message(message) else b""


def ai_request(request):
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
    rules = active_rules(flow)
    if not rules:
        return
    domain_blocked = any(
        rule.get("principle") == "allow" or de_patterns.any_match(rule.get("denied"), request.host)
        for rule in rules
    )
    if (domain_blocked or (any(rule.get("ai_auto") for rule in rules) and ai_request(request))
            or inspection.matches_content([request_text(request)], rules)):
        flow.response = blocked()


def responseheaders(flow):
    before = flow.response
    scan_response(flow, body=False)
    if flow.response is not before or not before.stream or not is_text_message(before):
        return
    rules = active_rules(flow)
    if not any(rule.get("request_deny") for rule in rules):
        return
    request = request_text(flow.request)
    header = response_text(before, b"")
    before.stream = inspection.StreamPrefix(3 * 65536, lambda prefix: inspection.matches_content(
        [request, header + prefix], rules,
    ))


def response(flow):
    if not flow.response.stream:
        scan_response(flow, body=True)


def scan_response(flow, body):
    rules = active_rules(flow)
    if not rules:
        return
    reply = flow.response
    raw = response_text(reply, text_body(reply) if body else b"")
    if inspection.matches_content([request_text(flow.request), raw], rules):
        flow.response = blocked()


def response_text(reply, body):
    first = f"{reply.http_version} {reply.status_code} {reply.reason}".encode()
    return inspection.message_text(first, reply.headers.fields, body)


def websocket_message(flow):
    rules = active_rules(flow)
    message = flow.websocket.messages[-1]
    if rules and message.is_text and inspection.matches_content([
        request_text(flow.request), message.content[:inspection.MAX_BODY],
    ], rules):
        message.drop()
    del flow.websocket.messages[:-1]
