from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from io import StringIO
from builtins import open as real_open
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
        for body, status in ((b"ordinary page", b"200"), ("<p>САЙТ ЗАКРЫТ ОГРАНИЧЕНИЯМИ ШКОЛЫ</p>".encode(), b"403")):
            with self.subTest(body=body):
                client, browser = socket.socketpair()
                upstream, origin = socket.socketpair()
                with client, browser, upstream, origin:
                    origin.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                                   b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
                    data = b"GET / HTTP/1.1\r\nHost: example.org\r\n\r\n"
                    with patch.object(relay.de_inspection.groups, "get",
                                      return_value=[{"request_deny": ["Сайт закрыт ограничениями школы"]}]):
                        relay.inspect_http(client, upstream, data, "example.org", 80)
                    client.shutdown(socket.SHUT_WR)
                    with browser.makefile("rb") as response:
                        page = response.read()
                    self.assertEqual(page.split()[1], status)
                    self.assertEqual(body in page, status == b"200")
