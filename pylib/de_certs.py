"""Сертификаты на лету для фильтра.

Для каждого вскрываемого узла нужен свой сертификат, подписанный нашим
центром. Делается это вызовом `openssl` - он есть на любой машине, и это
проще, чем тащить на МОС 12 сторонний пакет ради одной операции.

Готовые сертификаты складываются в память (`/run`), поэтому на диск не
попадают и после перезагрузки создаются заново.
"""

import os
import subprocess
import threading

CACHE_DIR = "/run/digital-examen/certs"
LOCK = threading.Lock()
MADE = {}


def safe_name(host):
    out = []
    for char in host.lower():
        out.append(char if (char.isalnum() or char in ".-") else "_")
    return "".join(out)[:120] or "unknown"


def wildcard(host):
    """Одна бумага на весь домен второго уровня, чтобы не плодить файлы."""
    parts = host.lower().strip(".").split(".")
    if len(parts) > 2 and not parts[-1].isdigit():
        return "*." + ".".join(parts[-2:]), ".".join(parts[-2:])
    return host, host


def make_config(path, host):
    names = [host]
    if host.startswith("*."):
        names.append(host[2:])
    else:
        names.append("*." + host)
    lines = [
        "[req]",
        "distinguished_name = name",
        "req_extensions = ext",
        "prompt = no",
        "[name]",
        "CN = " + host.replace("*.", ""),
        "[ext]",
        "basicConstraints = CA:FALSE",
        "keyUsage = digitalSignature,keyEncipherment",
        "extendedKeyUsage = serverAuth",
        "subjectAltName = " + ",".join("DNS:" + name for name in names),
    ]
    out = open(path, "w")
    out.write("\n".join(lines) + "\n")
    out.close()


def run(command):
    try:
        done = subprocess.run(command, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return 1
    return done.returncode


def cert_for(host, ca_cert, ca_key, days=397):
    """Пара файлов (сертификат, ключ) для узла. Делается один раз."""
    pattern, folder_name = wildcard(host)
    name = safe_name(folder_name)

    with LOCK:
        if name in MADE:
            return MADE[name]

        os.makedirs(CACHE_DIR, exist_ok=True)
        os.chmod(CACHE_DIR, 0o700)

        key_path = os.path.join(CACHE_DIR, name + ".key")
        cert_path = os.path.join(CACHE_DIR, name + ".crt")
        csr_path = os.path.join(CACHE_DIR, name + ".csr")
        conf_path = os.path.join(CACHE_DIR, name + ".conf")

        if os.path.exists(key_path) and os.path.exists(cert_path):
            MADE[name] = (cert_path, key_path)
            return MADE[name]

        make_config(conf_path, pattern)

        if run(["openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes",
                "-keyout", key_path, "-out", csr_path, "-config", conf_path]) != 0:
            return None, None

        if run(["openssl", "x509", "-req", "-in", csr_path,
                "-CA", ca_cert, "-CAkey", ca_key, "-CAcreateserial",
                "-out", cert_path, "-days", str(days),
                "-extfile", conf_path, "-extensions", "ext"]) != 0:
            return None, None

        os.chmod(key_path, 0o600)
        for leftover in (csr_path, conf_path):
            try:
                os.remove(leftover)
            except OSError:
                pass

        MADE[name] = (cert_path, key_path)
        return MADE[name]
