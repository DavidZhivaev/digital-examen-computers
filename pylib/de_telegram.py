import base64
from pathlib import Path
import socket
import threading
import time
import weakref
from urllib.parse import unquote, urlsplit

import de_common
import de_upstream

STATE = Path(de_common.RUN_DIR) / "tg-proxy"
LOCK = threading.Lock()
CONNECTIONS = weakref.WeakKeyDictionary()
DOMAINS = ("telegram.org", "telegram.me", "telegram.dog", "t.me", "cdn-telegram.org", "telegra.ph", "telesco.pe")


def matches(host):
    host = host.lower().rstrip(".")
    return any(host == suffix or host.endswith("." + suffix) for suffix in DOMAINS)


def parse_proxy(value):
    url = urlsplit(value)
    if (url.scheme != "http" or not url.hostname or not url.port
            or not url.username or not url.password or url.query or url.fragment
            or url.path not in ("", "/")):
        raise ValueError("Некорректные настройки прокси Telegram")
    return url


def connect(host, port, mark, timeout):
    if not matches(host):
        return None
    try:
        raw = STATE.read_text().strip()
        config = parse_proxy(raw)
    except FileNotFoundError:
        return None
    except ValueError:
        raise OSError("Некорректные настройки прокси Telegram") from None
    credential = f"{unquote(config.username)}:{unquote(config.password)}".encode()
    header = b"Proxy-Authorization: Basic " + base64.b64encode(credential)
    for family, kind, protocol, _, address in socket.getaddrinfo(config.hostname, config.port, type=socket.SOCK_STREAM):
        upstream = socket.socket(family, kind, protocol)
        try:
            upstream.settimeout(timeout)
            upstream.setsockopt(socket.SOL_SOCKET, socket.SO_MARK, mark)
            upstream.connect(address)
            if de_upstream.tunnel(upstream, host, port, header) == 200:
                with LOCK:
                    if STATE.read_text().strip() == raw:
                        CONNECTIONS[upstream] = raw
                        return upstream
        except OSError:
            pass
        upstream.close()
    raise OSError("Прокси Telegram недоступен")


def close_expired():
    while True:
        try:
            current = STATE.read_text().strip()
        except OSError:
            current = None
        with LOCK:
            for connection, proxy in list(CONNECTIONS.items()):
                if proxy != current:
                    try:
                        connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    connection.close()
                    CONNECTIONS.pop(connection, None)
        time.sleep(1)
