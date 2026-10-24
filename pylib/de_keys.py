import base64
import hashlib
import json
import os
import re

MEDIA_DIRS = ("/run/media", "/media", "/mnt")

PUBLIC_KEY_FILE = "/etc/digital-examen/key-public.pem"


def name_matches(filename, wanted):
    """Подходит ли имя файла под имя ключа.

    Браузер при повторном скачивании кладёт рядом `de-key (1).txt`, поэтому
    скобка с номером считается тем же именем. Регистр не важен.
    """
    stem, dot, ext = wanted.rpartition(".")
    if not dot:
        stem, ext = wanted, ""
    pattern = r"^" + re.escape(stem) + r"(?:[ _-]*\(\d+\)|[ _-]*\d+)?"
    if ext:
        pattern += r"\." + re.escape(ext)
    pattern += r"$"
    return re.match(pattern, filename.strip(), re.IGNORECASE) is not None


def find_in(folder, wanted):
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return ""
    found = []
    for name in names:
        if not name_matches(name, wanted):
            continue
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            found.append(path)
    if not found:
        return ""
    found.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    return found[0]


def find_key(name="de-key.txt"):
    for base in MEDIA_DIRS:
        if not os.path.isdir(base):
            continue
        try:
            tops = sorted(os.listdir(base))
        except OSError:
            continue
        for first in tops:
            top = os.path.join(base, first)
            if not os.path.isdir(top):
                continue
            found = find_in(top, name)
            if found:
                return found
            try:
                inner = sorted(os.listdir(top))
            except OSError:
                continue
            for second in inner:
                deeper = os.path.join(top, second)
                if not os.path.isdir(deeper):
                    continue
                found = find_in(deeper, name)
                if found:
                    return found
    return ""


def looks_like_token(line):
    parts = line.split(".")
    return len(parts) == 3 and all(parts) and " " not in line


def read_token(path):
    """Токен ключа из файла.

    В файле лежит JWT одной строкой, вокруг него комментарии. Формат
    `token=...` тоже понимаем - так удобнее читать файл глазами.
    """
    try:
        data = open(path).read()
    except IOError:
        return ""
    for line in data.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("token="):
            line = line.split("=", 1)[1].strip()
        if looks_like_token(line):
            return line
    return ""


def unpad(value):
    return value + "=" * (-len(value) % 4)


def token_body(token):
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        return json.loads(base64.urlsafe_b64decode(unpad(parts[1]).encode("ascii")))
    except (ValueError, TypeError):
        return {}


def read_public_key(path=None):
    path = path or PUBLIC_KEY_FILE
    if not os.path.exists(path):
        return b""
    try:
        return open(path, "rb").read()
    except IOError:
        return b""


def der_from_pem(pem):
    lines = []
    inside = False
    for line in pem.decode("ascii", "replace").splitlines():
        line = line.strip()
        if line.startswith("-----BEGIN"):
            inside = True
            continue
        if line.startswith("-----END"):
            break
        if inside:
            lines.append(line)
    try:
        return base64.b64decode("".join(lines))
    except (ValueError, TypeError):
        return b""


def der_read(data, start):
    tag = data[start]
    length = data[start + 1]
    head = start + 2
    if length & 0x80:
        count = length & 0x7F
        length = int.from_bytes(data[head:head + count], "big")
        head += count
    return tag, data[head:head + length], head + length


def rsa_numbers(pem):
    """Модуль и экспонента из открытого ключа в формате SubjectPublicKeyInfo.

    Разбираем вручную, чтобы на школьном компьютере не требовался
    сторонний пакет: проверка подписи нужна и тогда, когда интернета нет.
    """
    der = der_from_pem(pem)
    if not der:
        return 0, 0
    try:
        _, outer, _ = der_read(der, 0)
        _, _, after_alg = der_read(outer, 0)
        tag, bits, _ = der_read(outer, after_alg)
        if tag != 0x03:
            return 0, 0
        _, inner, _ = der_read(bits[1:], 0)
        _, modulus, after_mod = der_read(inner, 0)
        _, exponent, _ = der_read(inner, after_mod)
    except (IndexError, ValueError):
        return 0, 0
    return int.from_bytes(modulus, "big"), int.from_bytes(exponent, "big")


def check_signature(token, pem):
    """Проверить подпись RS256 своими силами.

    PKCS#1 v1.5: возводим подпись в открытую экспоненту и сравниваем с
    заранее известной обёрткой вокруг sha256 от подписанной части.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return False
    modulus, exponent = rsa_numbers(pem)
    if not modulus or not exponent:
        return False
    try:
        signature = base64.urlsafe_b64decode(unpad(parts[2]).encode("ascii"))
    except (ValueError, TypeError):
        return False

    size = (modulus.bit_length() + 7) // 8
    if len(signature) != size:
        return False

    opened = pow(int.from_bytes(signature, "big"), exponent, modulus)
    opened = opened.to_bytes(size, "big")

    digest = hashlib.sha256(parts[0].encode("ascii") + b"." + parts[1].encode("ascii")).digest()
    prefix = bytes.fromhex("3031300d060960864801650304020105000420")
    wanted = b"\x00\x01" + b"\xff" * (size - len(prefix) - len(digest) - 3) + b"\x00" + prefix + digest
    if len(opened) != len(wanted):
        return False

    same = 0
    for left, right in zip(opened, wanted):
        same |= left ^ right
    return same == 0


def verify(token, pem=None, issuer="digital-examen", audience="digital-examen-computers"):
    """Разобранный токен, если подпись сходится. Иначе пустой словарь.

    Срок жизни у ключа не проверяется: его нет. Ключ гаснет на сайте -
    отзывом, выдачей нового и сменой пароля владельца.
    """
    if pem is None:
        pem = read_public_key()
    if not pem or not token:
        return {}
    if not check_signature(token, pem):
        return {}
    body = token_body(token)
    if body.get("iss") != issuer:
        return {}
    if body.get("aud") != audience:
        return {}
    if not body.get("jti") or not body.get("sub"):
        return {}
    return body


def parse_key(path):
    """Содержимое файла ключа: токен и то, что в нём написано."""
    token = read_token(path)
    if not token:
        return {}
    body = verify(token)
    if not body:
        body = token_body(token)
        body["verified"] = False
    else:
        body["verified"] = True
    body["token"] = token
    return body
