"""Route filtered web connections through the school HTTP proxy."""
import ipaddress
import re
import socket


class ProxyDenied(Exception):
    """The proxy responded with a destination-specific refusal."""


def authority(host, port):
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"[a-zA-Z0-9._-]+", host):
            raise ValueError("недопустимое имя сайта") from None
        return f"{host}:{port}"
    return f"[{address}]:{port}" if address.version == 6 else f"{address}:{port}"


def tunnel(upstream, host, port):
    target = authority(host, port)
    upstream.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode("ascii"))
    # Before TLS starts, the proxy can send only the CONNECT response.
    with upstream.makefile("rb") as response:
        line = response.readline(4097)
        if not line or len(line) > 4096:
            raise OSError("прокси не ответил корректно")
        fields = line.split()
        if len(fields) < 2 or not fields[0].startswith(b"HTTP/") or not fields[1].isdigit():
            raise OSError("некорректный ответ прокси")
        for _ in range(100):
            line = response.readline(8193)
            if line == b"\r\n":
                return int(fields[1])
            if not line or len(line) > 8192:
                break
    raise OSError("неполные заголовки прокси")


TOKEN = re.compile(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


def http_request(data, host, port):
    head, separator, pending = data.partition(b"\r\n\r\n")
    if not separator:
        raise ValueError("неполный запрос HTTP")
    lines = head.split(b"\r\n")
    method, path, version = lines[0].split()
    if not TOKEN.fullmatch(method) or not path.startswith(b"/") or version not in (b"HTTP/1.0", b"HTTP/1.1"):
        raise ValueError("неподдерживаемый запрос HTTP")
    headers = []
    lengths = []
    for line in lines[1:]:
        name, separator, value = line.partition(b":")
        if not separator or not TOKEN.fullmatch(name) or any(byte < 32 and byte != 9 for byte in value):
            raise ValueError("некорректный заголовок HTTP")
        name = name.lower()
        if name == b"transfer-encoding":
            raise ValueError("потоковая загрузка через HTTP-прокси не поддерживается")
        if name == b"content-length":
            if not value.strip().isdigit():
                raise ValueError("некорректный размер тела HTTP")
            lengths.append(int(value))
        if name not in (b"connection", b"host") and not name.startswith(b"proxy-"):
            headers.append(line)
    if len(lengths) > 1:
        raise ValueError("повтор Content-Length")
    length = lengths[0] if lengths else 0
    target = authority(host, port).encode("ascii")
    headers.extend((b"Host: " + target, b"Connection: close"))
    first = b" ".join((method, b"http://" + target + path, version))
    body = pending[:length]
    return b"\r\n".join([first, *headers]) + b"\r\n\r\n" + body, length - len(body)


def send_body(client, upstream, remaining):
    try:
        while remaining:
            chunk = client.recv(min(remaining, 65536))
            if not chunk:
                break
            upstream.sendall(chunk)
            remaining -= len(chunk)
    except OSError:
        pass
