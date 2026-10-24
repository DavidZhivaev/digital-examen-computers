import base64
import hashlib
import hmac
import json
import os
import time

VERSION = 2
CHAIN = "bcrypt-scrypt-pbkdf2"

DEFAULT_KDF = {
    "scrypt_n": 17,
    "scrypt_r": 8,
    "scrypt_p": 1,
    "pbkdf2": 1200000,
}

MAX_MEMORY = 512 * 1024 * 1024


def read_config(path="/etc/digital-examen/computers.conf"):
    values = {}
    if not os.path.exists(path):
        return values
    data = open(path)
    for line in data:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name.strip()] = value
    data.close()
    return values


def read_text(path):
    if not path or not os.path.exists(path):
        return ""
    return open(path).read().strip()


def same(first, second):
    return hmac.compare_digest(str(first), str(second))


def bcrypt_hash(password, prefix):
    """bcrypt от пароля с солью и стоимостью, которые дал сайт.

    Сначала системный crypt: на МОС 12 он умеет $2b и считает на C. Если не
    умеет - модуль bcrypt, который доставляет установка. Хеша пароля в
    выгрузке нет, есть только эта соль, поэтому путь к проверке один:
    посчитать bcrypt заново от того, что набрал человек.
    """
    if not prefix or not prefix.startswith("$2"):
        return ""

    try:
        import crypt
        out = crypt.crypt(password, prefix)
        if out and out.startswith(prefix):
            return out
    except Exception:
        pass

    try:
        import bcrypt
        return bcrypt.hashpw(password.encode("utf-8"), prefix.encode("ascii")).decode("ascii")
    except Exception:
        return ""


def stretch(bcrypt_value, salt, kdf=None):
    rules = dict(DEFAULT_KDF)
    if kdf:
        rules.update(kdf)
    seed = bytes.fromhex(salt) if isinstance(salt, str) else salt
    step = hashlib.scrypt(
        bcrypt_value.encode("utf-8"),
        salt=seed,
        n=2 ** int(rules["scrypt_n"]),
        r=int(rules["scrypt_r"]),
        p=int(rules["scrypt_p"]),
        maxmem=MAX_MEMORY,
        dklen=64,
    )
    return hashlib.pbkdf2_hmac("sha512", step, seed, int(rules["pbkdf2"]), 32).hex()


def bind_verifier(key, verifier):
    return hmac.new(bytes.fromhex(key), bytes.fromhex(verifier), hashlib.sha256).hexdigest()


def password_matches(secret, entry, password):
    """Тот ли пароль. Один прогон стоит примерно две секунды.

    Порядок: bcrypt с солью сайта, затем scrypt на 128 МБ, затем 1 200 000
    итераций pbkdf2, затем привязка к секрету этого компьютера. Любой из
    шагов нельзя пропустить, поэтому перебор по украденной выгрузке стоит
    столько же, сколько честная проверка.
    """
    if str(entry.get("chain", CHAIN)) != CHAIN:
        return False

    hashed = bcrypt_hash(password, str(entry.get("bcrypt", "")))
    if not hashed:
        return False

    value = stretch(hashed, entry.get("salt", ""), entry.get("kdf"))
    return same(bind_verifier(secret, value), entry.get("verifier", ""))


def keystream(key, nonce, length):
    out = b""
    counter = 0
    while len(out) < length:
        out += hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return out[:length]


def subkeys(secret, nonce):
    root = bytes.fromhex(secret)
    cipher_key = hmac.new(root, b"de-vault-cipher" + nonce, hashlib.sha256).digest()
    tag_key = hmac.new(root, b"de-vault-tag" + nonce, hashlib.sha256).digest()
    return cipher_key, tag_key


def seal(secret, payload):
    nonce = os.urandom(16)
    cipher_key, tag_key = subkeys(secret, nonce)
    plain = json.dumps(payload).encode("utf-8")
    data = bytes(a ^ b for a, b in zip(plain, keystream(cipher_key, nonce, len(plain))))
    tag = hmac.new(tag_key, nonce + data, hashlib.sha256).hexdigest()
    return json.dumps({
        "version": VERSION,
        "nonce": nonce.hex(),
        "tag": tag,
        "data": base64.b64encode(data).decode("ascii"),
    })


def unseal(secret, blob):
    box = json.loads(blob)
    nonce = bytes.fromhex(box["nonce"])
    data = base64.b64decode(box["data"])
    cipher_key, tag_key = subkeys(secret, nonce)
    tag = hmac.new(tag_key, nonce + data, hashlib.sha256).hexdigest()
    if not same(tag, box.get("tag", "")):
        return None
    plain = bytes(a ^ b for a, b in zip(data, keystream(cipher_key, nonce, len(data))))
    return json.loads(plain.decode("utf-8"))


def write_private(path, text):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
        os.chmod(folder, 0o700)
    temporary = path + ".new"
    out = open(temporary, "w")
    out.write(text)
    out.close()
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def load_bundle(path, secret):
    blob = read_text(path)
    if not blob or not secret:
        return None
    try:
        return unseal(secret, blob)
    except (ValueError, KeyError):
        return None


def bundle_fresh(bundle, max_age_days):
    if not bundle:
        return False
    made = int(bundle.get("made", 0))
    if made <= 0:
        return False
    return time.time() - made <= max_age_days * 86400


def find_entry(bundle, login):
    if not bundle:
        return None
    wanted = str(login).lower()
    for entry in bundle.get("accounts", []):
        if str(entry.get("login", "")).lower() == wanted:
            return entry
    return None


def merge(bundle, part):
    """Приложить присланные изменения к тому, что уже лежит на компьютере."""
    if bundle is None or part.get("full"):
        accounts = {}
    else:
        accounts = {}
        for entry in bundle.get("accounts", []):
            accounts[str(entry.get("login", "")).lower()] = entry

    for entry in part.get("accounts", []):
        accounts[str(entry.get("login", "")).lower()] = entry
    for login in part.get("removed", []):
        accounts.pop(str(login).lower(), None)

    newest = 0
    for entry in accounts.values():
        newest = max(newest, int(entry.get("updated", 0) or 0))

    return {
        "version": VERSION,
        "computer": part.get("computer", ""),
        "made": int(part.get("made", time.time())),
        "since": newest,
        "accounts": list(accounts.values()),
    }
