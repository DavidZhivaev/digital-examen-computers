from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from io import StringIO
from builtins import open as real_open
import base64
import gzip
import socket
import sys
import unittest
from unittest.mock import patch

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
