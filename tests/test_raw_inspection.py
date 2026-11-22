from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from io import StringIO
from builtins import open as real_open
import asyncio
import json
import base64
import gzip
import socket
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pylib"))
loader = SourceFileLoader("relay", str(ROOT / "bin/de-net"))
spec = spec_from_loader(loader.name, loader)
relay = module_from_spec(spec)
def open_test_config(file, *args, **kwargs):
    if file == "/etc/digital-examen/computers.conf":
        return StringIO("")
    return real_open(file, *args, **kwargs)


with patch("builtins.open", side_effect=open_test_config):
    import de_common
    import de_ansible_network
    import de_inspection
    loader.exec_module(relay)


class RawInspectionTests(unittest.TestCase):
    def test_group_priorities_and_blacklist_union(self):
        first = {"priority": 1, "principle": "allow", "always_allow": ["a.example"]}
        second = {"priority": 50, "denied": ["a.example", "b.example"]}
        rules = de_inspection.load_groups(json.dumps({"groups": [second, first]}))
        for host, denied in (("a.example", False), ("b.example", True), ("c.example", True)):
            self.assertEqual(de_inspection.url_blocked(host, "https://" + host, rules), denied)

        first = {"priority": 1, "denied": ["c.example"]}
        rules = de_inspection.load_groups(json.dumps({"groups": [second, first]}))
        for host in ("a.example", "b.example", "c.example"):
            self.assertTrue(de_inspection.url_blocked(host, "https://" + host, rules))

    def test_inspector_sends_block_page_for_chunked_content_and_wiki_topics(self):
        try:
            from mitmproxy import http
        except ImportError:
            self.skipTest("mitmproxy is required for the HTTPS inspection hooks")
        with patch.object(de_common, "setting", side_effect=lambda key, default: default):
            import de_inspection_addon as addon

        for url, page, rules in (
            ("https://example.org/", b"<p>Denied phrase</p>", [{"request_deny": ["denied phrase"]}]),
            ("https://ru.wikipedia.org/wiki/AI", "<title>Искусственный интеллект — Википедия</title>".encode(),
             [{"ai_auto": True}]),
        ):
            flow = Mock(request=http.Request.make("GET", url))
            flow.response = http.Response.make(200, page, {"content-type": "text/html"})
            del flow.response.headers["content-length"]
            flow.response.stream = True
            with patch.object(de_inspection.groups, "get", return_value=rules):
                addon.responseheaders(flow)
                self.assertFalse(flow.response.stream)
                asyncio.run(addon.response(flow))
            self.assertEqual(flow.response.status_code, 403)
            self.assertIn(b"<html", flow.response.raw_content.lower())

        flow = Mock(request=http.Request.make("GET", "https://yandex.ru/search/?text=Denied+phrase"),
                    response=None)
        rules = [{"request_deny": ["denied phrase"], "url_allow": ["yandex.ru/search*"]}]
        with patch.object(de_inspection.groups, "get", return_value=rules):
            addon.request(flow)
        self.assertEqual(flow.response.status_code, 403)

    def test_learned_endpoints_follow_active_terms_and_expire(self):
        import de_learned

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.json"
            cache = de_inspection.CachedFile(path, de_learned.json.loads, {})
            url = de_learned.endpoint("https://example.org/answer?private=value")
            with patch.multiple(de_learned, LOCAL=path, local=cache), patch("de_learned.time.time") as now:
                now.return_value = 100
                de_learned.remember(url, ("test",))
                self.assertEqual(url, "https://example.org/answer")
                self.assertTrue(de_learned.blocked(url, ("test",)))
                self.assertFalse(de_learned.blocked(url, ("different",)))
                now.return_value = 86501
                self.assertFalse(de_learned.blocked(url, ("test",)))
        self.assertEqual(de_learned.endpoint("https://yandex.ru/search/?text=test"), "")

    def test_origin_transport_accepts_only_the_inspector_side(self):
        handler = relay.InspectionOrigin.__new__(relay.InspectionOrigin)
        handler.client_address = ("127.0.0.1", 39000)
        handler.request = Mock()
        with patch.object(relay, "owner_of", return_value="") as owner:
            handler.handle()
        owner.assert_called_once_with(handler.client_address, {0}, "0100007F:%04X" % relay.de_inspection.ORIGIN_PORT)
        handler.request.settimeout.assert_not_called()

        upstream, origin = socket.socketpair()
        with upstream, origin:
            with patch.object(relay, "owner_of", return_value="root"), \
                    patch.object(relay, "read_hello", return_value=b"CONNECT sdo.1580.ru:443 HTTP/1.1\r\n\r\n"), \
                    patch.object(relay, "connect_web", return_value=upstream) as connect, \
                    patch.object(relay, "bridge") as bridge:
                handler.handle()
        connect.assert_called_once_with("sdo.1580.ru", 443, False, timeout=4)
        handler.request.sendall.assert_called_once_with(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        bridge.assert_called_once_with(handler.request, upstream)

    def test_response_phrase_in_raw_http_body(self):
        phrase = "<p>САЙТ ЗАКРЫТ ОГРАНИЧЕНИЯМИ ШКОЛЫ</p>".encode()
        for body, status, allowed in ((b"ordinary page", b"200", []), (phrase, b"403", []),
                                      (phrase, b"200", ["*.1580.ru"])):
            with self.subTest(body=body):
                client, browser = socket.socketpair()
                upstream, origin = socket.socketpair()
                with client, browser, upstream, origin:
                    origin.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                                   b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
                    data = b"GET / HTTP/1.1\r\nHost: sdo.1580.ru\r\n\r\n"
                    with patch.object(relay.de_inspection.groups, "get",
                                      return_value=[{"request_deny": ["Сайт закрыт ограничениями школы"],
                                                     "always_allow": allowed}]):
                        relay.inspect_http(client, upstream, data, "sdo.1580.ru", 80)
                    client.shutdown(socket.SHUT_WR)
                    with browser.makefile("rb") as response:
                        page = response.read()
                    self.assertEqual(page.split()[1], status)
                    self.assertEqual(body in page, status == b"200")


    def test_search_queries_follow_same_url_whitelist(self):
        rules = [{"principle": "allow", "url_allow": ["yandex.ru/search*"]}]
        for query in ("123", "321"):
            self.assertFalse(relay.de_inspection.url_blocked(
                "yandex.ru", "https://yandex.ru/search/?text=" + query, rules
            ))

    def test_full_url_rule_matches_origin_instead_of_a_url_in_the_query(self):
        blocked = relay.de_inspection.url_blocked
        rules = [{"principle": "allow", "url_allow": ["https://*.1580.ru/login/*"]}]
        self.assertFalse(blocked("sdo.1580.ru", "https://sdo.1580.ru/login/index.php?next=/", rules))
        self.assertTrue(blocked("other.example", "https://other.example/?next=https://sdo.1580.ru/login/", rules))
        self.assertTrue(blocked("sdo.1580.ru", "https://sdo.1580.ru/help?next=/login/", rules))

    def test_encoded_request_and_compressed_response_can_match(self):
        rules = [{"request_allow": ["ИИ"], "request_deny": ["Режим ИИ"]}]
        encoded = b"GET /?text=%D0%A0%D0%B5%D0%B6%D0%B8%D0%BC+%D0%98%D0%98"
        compressed = gzip.compress("Режим ИИ".encode())
        self.assertTrue(relay.de_inspection.matches_content([encoded], rules))
        prefix = relay.de_inspection.text_prefix(compressed, "gzip")
        self.assertTrue(relay.de_inspection.matches_content([prefix], rules))


    def test_search_challenge_inherits_only_the_matching_yandex_search_rule(self):
        rules = [{"principle": "allow", "url_allow": ["yandex.ru/search*"]}]
        for original, blocked in (
            ("https://yandex.ru/search?text=321", False),
            ("https://yandex.ru/alice/", True),
            ("https://external.example/search?text=321", True),
        ):
            encoded = base64.urlsafe_b64encode(original.encode()).decode().rstrip("=")
            challenge = "https://yandex.ru/showcaptcha?retpath=" + encoded + "_" + "f" * 32
            self.assertEqual(relay.de_inspection.url_blocked("yandex.ru", challenge, rules), blocked)


    def test_phrase_split_by_markup_and_escaped_russian_text(self):
        rules = [{"request_deny": ["Новый чат"]}]
        self.assertTrue(relay.de_inspection.matches_content(
            ["<b>Новый</b><span>чат</span>".encode()], rules
        ))
        self.assertTrue(relay.de_inspection.matches_content(
            [br"\u041d\u043e\u0432\u044b\u0439 \u0447\u0430\u0442"], rules
        ))


    def test_google_web_search_preserves_query_and_specialized_tabs(self):
        rewrite = relay.de_inspection.google_web_search_path
        self.assertEqual(rewrite("www.google.com", "/search?q=погода&hl=ru"),
                         "/search?q=%D0%BF%D0%BE%D0%B3%D0%BE%D0%B4%D0%B0&hl=ru&udm=14")
        for path in ("/search?q=weather&udm=14", "/search?q=weather&udm=2",
                     "/search?q=weather&udm=50", "/search?q=weather&tbm=isch", "/settings"):
            self.assertEqual(rewrite("www.google.com", path), path)
        self.assertEqual(rewrite("google.com.example.org", "/search?q=weather"), "/search?q=weather")


    def test_yandex_styles_are_added_once_after_the_doctype_without_relaxing_scripts(self):
        inspection = relay.de_inspection
        stream = inspection.YandexStyleStream(True)
        page = stream(b"<!doc") + stream(b"type html><html>") + stream(b"<body>links") + stream(b"")
        self.assertTrue(page.startswith(b"<!doctype html><style>"))
        self.assertEqual(page.count(inspection.YANDEX_STYLE_TAG), 1)
        self.assertIn(b"<body>links", page)
        policy = "default-src 'self'; script-src 'none'; style-src 'none'"
        result = inspection.yandex_style_policy(policy)
        self.assertIn("script-src 'none'", result)
        self.assertTrue(result.endswith("style-src-elem " + inspection.YANDEX_STYLE_HASH))
        inline_policy = "style-src 'unsafe-inline'; script-src 'none'"
        self.assertEqual(inspection.yandex_style_policy(inline_policy), inline_policy)

    def test_yandex_stream_does_not_emit_empty_http_chunks(self):
        inspection = relay.de_inspection
        transform = inspection.stream_chunks(inspection.YandexStyleStream(True))
        self.assertEqual(transform(b"<!doc"), [])
        parts = transform(b"type html><body>links")
        self.assertEqual(parts, [b"<!doctype html>" + inspection.YANDEX_STYLE_TAG + b"<body>links"])
        self.assertEqual(transform(b""), [])


    def test_yandex_only_removes_complete_answer_nodes_and_keeps_the_results_shell(self):
        inspection = relay.de_inspection
        page = (
            '<!doctype html><html><head></head><body><div class="FuturisSearchWrapper">'
            '<li class="serp-item serp-item__futuris-snippet"><div>Скрытый ответ ИИ</div>'
            '<script>var ai="privateAnswer";</script></li>'
            '<a class="HeaderNav-Tab" href="/search?promo=force_neuro">Алиса</a>'
            '<a href="/ordinary">Обычная ссылка</a></div></body></html>'
        ).encode()
        buffered = inspection.filter_yandex_html(page)
        stream = inspection.YandexStyleStream(True)
        streamed = b"".join(stream(page[i:i + 7]) for i in range(0, len(page), 7)) + stream(b"")
        for result in (buffered, streamed):
            self.assertEqual(len(result), len(page) + len(inspection.YANDEX_STYLE_TAG))
            self.assertIn("Обычная ссылка".encode(), result)
            self.assertIn(b'class="FuturisSearchWrapper"', result)
        self.assertNotIn("Скрытый ответ ИИ".encode(), buffered)
        self.assertNotIn(b"privateAnswer", buffered)
        self.assertEqual(streamed.replace(inspection.YANDEX_STYLE_TAG, b"", 1), page)

        malformed = b'<li class="serp-item serp-item__futuris-snippet"><a>ordinary results</a>'
        self.assertIn(b"<a>ordinary results</a>", inspection.filter_yandex_html(malformed))

    def test_yandex_new_hashed_ai_class_is_removed(self):
        inspection = relay.de_inspection
        page = (b'<div class="FuturisSearchWrapper"><li class="ym2fgR1955MWD '
                b'ym2fgR1955MWD__futuris-snippet"><div>AI answer</div></li>'
                b'<li class="ordinary-result"><a>normal result</a></li></div>')
        filtered = inspection.filter_yandex_html(page)
        self.assertNotIn(b"AI answer", filtered)
        self.assertIn(b"normal result", filtered)
        self.assertIn(b'li[class*="__futuris-snippet"]', inspection.YANDEX_STYLE_TAG)

    def test_body_mime_and_js_size_filter(self):
        inspection = relay.de_inspection
        for mime in ("text/html; charset=utf-8", "text/plain", "application/vnd.example+json",
                     "application/xml", "application/xhtml+xml"):
            self.assertEqual(inspection.body_scan_limit(mime), inspection.MAX_BODY)
        for mime in ("text/css", "image/svg+xml", "application/pdf"):
            self.assertEqual(inspection.body_scan_limit(mime, 100), 0)
        self.assertEqual(inspection.body_scan_limit("application/javascript", "32768"), 32768)
        self.assertEqual(inspection.body_scan_limit("text/javascript", "32769"), 0)
        self.assertEqual(inspection.body_scan_limit("text/javascript"), 0)
        self.assertEqual(inspection.text_prefix(gzip.compress(b"x" * 32769), "gzip", 32768), b"")
