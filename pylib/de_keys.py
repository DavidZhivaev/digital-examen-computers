import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile


PUBLIC_KEY_FILE = "/etc/digital-examen/key-public.pem"
SAVED_KEY = "/run/digital-examen/de-key.txt"


def name_matches(filename, wanted):
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
        if not os.path.islink(path) and os.path.isfile(path) and os.path.getsize(path) <= 65536:
            found.append(path)
    if not found:
        return ""
    found.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    return found[0]


def usb_partitions():
    try:
        output = subprocess.check_output(
            ["lsblk", "-J", "-o", "PATH,TRAN,MOUNTPOINT,FSTYPE,TYPE"],
            stderr=subprocess.DEVNULL, timeout=10)
        tree = json.loads(output.decode("utf-8", "replace"))
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    out = []
    for disk in tree.get("blockdevices", []):
        if disk.get("type") != "disk":
            continue
        if disk.get("tran") != "usb":
            continue
        parts = disk.get("children") or [disk]
        for part in parts:
            if part.get("fstype"):
                out.append(part)
    return out


def find_on_usb(name):
    for part in usb_partitions():
        if part.get("mountpoint"):
            found = find_in(part["mountpoint"], name)
            if found and (saved := save_key(found)):
                return saved
            continue
        with tempfile.TemporaryDirectory(prefix="de-usb-") as place:
            if subprocess.call(["mount", "-o", "ro,nosuid,nodev,noexec", part["path"], place],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0:
                continue
            try:
                found = find_in(place, name)
                if found and (saved := save_key(found)):
                    return saved
            finally:
                subprocess.run(["umount", place], check=True)
    return ""


def save_key(path):
    if not read_token(path):
        return ""
    os.makedirs(os.path.dirname(SAVED_KEY), mode=0o755, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=os.path.dirname(SAVED_KEY), delete=False) as output:
        with open(path, "rb") as source:
            shutil.copyfileobj(source, output, 8192)
    os.replace(output.name, SAVED_KEY)
    return SAVED_KEY


def find_key(name="de-key.txt"):
    return find_on_usb(name)


def looks_like_token(line):
    parts = line.split(".")
    return len(parts) == 3 and all(parts) and " " not in line


def read_token(path):
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
