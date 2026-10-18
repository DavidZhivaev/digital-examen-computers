"""Фильтр с расшифровкой: что видно внутри запроса ученика.

Зачем вообще. Без расшифровки о странице известно только имя узла. Этого
мало и для ограничений («закрыть переписку с ИИ, оставив поиск»), и для
классификатора — он работает вслепую, по домену.

Почему на компьютере, а не на школьном сервере. Расшифровывать трафик 1440
машин на восьми ядрах арифметически невозможно: это десятки гигабит и
тысячи рукопожатий в секунду. На самом компьютере это один человек и один
браузер, и работа размазывается по всей школе.

Что бывает с запросом:

1. узел в списке «не вскрывать» (`no-open.txt`) — обычный туннель, как
   раньше: видно только имя узла. Банки, госуслуги, медицина и программы,
   которые проверяют сертификат сами;
2. иначе — мы выдаём браузеру свою бумагу на этот узел, читаем запрос и
   ответ, и если это страница, отдаём её текст на решение `de-policy`.

Ключ нашего центра лежит под root и компьютер не покидает. Расшифровка
разрешена школьным локальным актом.
"""

import re
import socket
import ssl
import threading

import de_certs

MAX_BODY = 262144
TEXT_TYPES = ("text/html", "application/xhtml")

TAG = re.compile(r"<[^>]+>")
SPACE = re.compile(r"\s+")
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
SCRIPT = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


def page_title(html):
    found = TITLE.search(html)
    if not found:
        return ""
    return SPACE.sub(" ", TAG.sub(" ", found.group(1))).strip()[:300]


def page_text(html, limit=4000):
    """Текст страницы без разметки — то, что увидит классификатор."""
    body = SCRIPT.sub(" ", html)
    body = TAG.sub(" ", body)
    body = body.replace("&nbsp;", " ").replace("&amp;", "&")
    body = body.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return SPACE.sub(" ", body).strip()[:limit]


def client_context(cert_path, key_path):
    """Контекст, которым мы притворяемся сайтом перед браузером."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    return context


def upstream_context():
    """Контекст, которым мы ходим к настоящему сайту.

    Сертификат сайта проверяем по-настоящему: раз мы встали в середину, то
    проверять его теперь наша работа, а не браузера.
    """
    context = ssl.create_default_context()
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def read_head(sock, limit=65536):
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = sock.recv(4096)
        if not chunk:
            return head, b""
        head += chunk
        if len(head) > limit:
            return head, b""
    split = head.index(b"\r\n\r\n")
    return head[:split], head[split + 4:]


def header_value(head, name):
    wanted = name.lower() + ":"
    for line in head.decode("latin-1", "replace").split("\r\n")[1:]:
        if line.lower().startswith(wanted):
            return line.split(":", 1)[1].strip()
    return ""


def request_path(head):
    try:
        return head.decode("latin-1").split("\r\n", 1)[0].split(" ")[1]
    except (IndexError, UnicodeDecodeError):
        return "/"


def read_body(sock, head, first):
    """Тело ответа: по длине или кусками. Больше MAX_BODY не берём."""
    length = header_value(head, "content-length")
    chunked = "chunked" in header_value(head, "transfer-encoding").lower()

    body = first
    if length.isdigit():
        want = min(int(length), MAX_BODY)
        while len(body) < want:
            chunk = sock.recv(65536)
            if not chunk:
                break
            body += chunk
        return body

    if chunked:
        while len(body) < MAX_BODY and b"0\r\n\r\n" not in body[-16:]:
            chunk = sock.recv(65536)
            if not chunk:
                break
            body += chunk
        return body

    return body


def unchunk(body):
    out = b""
    rest = body
    while b"\r\n" in rest:
        size_text, rest = rest.split(b"\r\n", 1)
        try:
            size = int(size_text.split(b";")[0], 16)
        except ValueError:
            return body
        if size == 0:
            break
        out += rest[:size]
        rest = rest[size + 2:]
    return out


def decode_body(head, body):
    """Текст ответа. Сжатое не разжимаем — просто не смотрим в него."""
    if "chunked" in header_value(head, "transfer-encoding").lower():
        body = unchunk(body)
    if header_value(head, "content-encoding"):
        return ""
    return body.decode("utf-8", "replace")


def is_page(head):
    kind = header_value(head, "content-type").lower()
    return any(kind.startswith(name) for name in TEXT_TYPES)


def pipe(source, target):
    try:
        while True:
            chunk = source.recv(65536)
            if not chunk:
                break
            target.sendall(chunk)
    except OSError:
        pass
    try:
        target.shutdown(socket.SHUT_WR)
    except OSError:
        pass


def both_ways(first, second):
    thread = threading.Thread(target=pipe, args=(first, second))
    thread.daemon = True
    thread.start()
    pipe(second, first)
    thread.join(timeout=5)


def open_and_watch(client, host, port, upstream, ca_cert, ca_key, look):
    """Встать в середину одного соединения.

    `look` — то, что решает, пускать ли страницу: получает адрес, заголовок
    и текст, отвечает «пропустить» или «закрыть». Решение принимается по
    каждому запросу внутри соединения, а не один раз по имени узла.
    """
    cert_path, key_path = de_certs.cert_for(host, ca_cert, ca_key)
    if not cert_path:
        return False

    try:
        inner_client = client_context(cert_path, key_path).wrap_socket(client, server_side=True)
    except (ssl.SSLError, OSError):
        return True

    try:
        inner_upstream = upstream_context().wrap_socket(upstream, server_hostname=host)
    except (ssl.SSLError, OSError):
        try:
            inner_client.close()
        except OSError:
            pass
        return True

    serve(inner_client, inner_upstream, host, port, look)
    return True


def serve(client, upstream, host, port, look):
    """Запрос за запросом, пока соединение живо.

    Соединение закрываем, как только о том просит любая из сторон. Без
    этого цикл ждал бы следующий запрос от браузера, который уже ушёл, и
    поток висел бы до самого выхода из сеанса.
    """
    try:
        client.settimeout(60)
        upstream.settimeout(60)
    except OSError:
        pass

    while True:
        try:
            head, rest = read_head(client)
        except (OSError, ssl.SSLError):
            return
        if not head:
            return

        path = request_path(head)
        url = "https://%s%s" % (host, path)
        client_closes = header_value(head, "connection").lower() == "close"

        if look(url, "", "") == "block":
            try:
                client.sendall(blocked_answer())
            except OSError:
                pass
            return

        try:
            upstream.sendall(head + b"\r\n\r\n" + rest)
        except OSError:
            return

        try:
            answer_head, answer_rest = read_head(upstream)
        except (OSError, ssl.SSLError):
            return
        if not answer_head:
            return

        if not is_page(answer_head):
            try:
                client.sendall(answer_head + b"\r\n\r\n" + answer_rest)
            except OSError:
                return
            both_ways(client, upstream)
            return

        answer_closes = header_value(answer_head, "connection").lower() == "close"

        body = read_body(upstream, answer_head, answer_rest)
        html = decode_body(answer_head, body)

        if html and look(url, page_title(html), page_text(html)) == "block":
            try:
                client.sendall(blocked_answer())
            except OSError:
                pass
            return

        try:
            client.sendall(answer_head + b"\r\n\r\n" + body)
        except OSError:
            return

        if client_closes or answer_closes:
            return


BLOCK_TEXT = "Закрыто ограничениями школы\n".encode("utf-8")


def blocked_answer():
    return (
        b"HTTP/1.1 403 Forbidden\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"Content-Length: " + str(len(BLOCK_TEXT)).encode() + b"\r\n"
        b"Connection: close\r\n\r\n" + BLOCK_TEXT
    )
