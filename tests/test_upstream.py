import base64
import socket
import sys
import unittest
from pathlib import Path
from threading import Thread

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pylib"))

import de_upstream


class UpstreamTests(unittest.TestCase):
    def test_forwarding_sends_only_the_filtered_http_request(self):
        request = (
            b"POST /submit HTTP/1.1\r\nHost: example.org\r\nContent-Length: 3\r\n\r\nabc"
            b"GET http://other.example/ HTTP/1.1\r\n\r\n"
        )
        forward, remaining = de_upstream.http_request(request, "example.org", 80)
        self.assertTrue(forward.startswith(b"POST http://example.org:80/submit HTTP/1.1\r\n"))
        self.assertTrue(forward.endswith(b"\r\n\r\nabc"))
        self.assertEqual(remaining, 0)

    def test_credential_has_a_proof_instead_of_the_device_secret(self):
        header = de_upstream.authorization("2k-418-1", "private-secret", "student")
        fields = base64.b64decode(header.split()[-1]).decode().split(":")
        self.assertEqual(fields[:3], ["student", "de1", "2k-418-1"])
        self.assertEqual(len(fields[-1]), 64)
        self.assertNotIn("private-secret", ":".join(fields))

    def test_ambiguous_http_body_is_rejected(self):
        headers = b"POST / HTTP/1.1\r\nHost: example.org\r\nContent-Length: 3\r\n"
        with self.assertRaises(ValueError):
            de_upstream.http_request(headers + b"Content-Length: 4\r\n\r\n", "example.org", 80)
        with self.assertRaises(ValueError):
            de_upstream.http_request(headers + b"Transfer-Encoding: chunked\r\n\r\n", "example.org", 80)

    def test_connect_keeps_proxy_refusal_distinct_from_transport_failure(self):
        for status in (200, 403, 407, 503):
            with self.subTest(status=status):
                client, proxy = socket.socketpair()
                received = []

                def respond():
                    received.append(proxy.recv(4096))
                    proxy.sendall(f"HTTP/1.1 {status} Response\r\n\r\n".encode())

                with client, proxy:
                    client.settimeout(2)
                    proxy.settimeout(2)
                    responder = Thread(target=respond)
                    responder.start()
                    self.assertEqual(de_upstream.tunnel(client, "example.org", 443), status)
                    responder.join()
                self.assertTrue(received[0].startswith(b"CONNECT example.org:443 HTTP/1.1\r\n"))
