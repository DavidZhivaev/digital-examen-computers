import fcntl
import os
import pwd
import shutil
from pathlib import Path
import socket
import ssl
import sys
import subprocess
import tempfile
import time

from de_common import people, write_file
from de_firefox_policy import inspection_ca

CA_DIR = Path("/var/lib/de-inspect")
ANCHOR = Path("/etc/pki/ca-trust/source/anchors/digital-examen-inspection.crt")
HOSTS = Path("/etc/digital-examen/inspect-hosts")


def provision(directory):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid != 0:
        raise ValueError("Неверный владелец каталога CA")
    directory.chmod(0o700)
    combined = directory / "mitmproxy-ca.pem"
    if combined.is_symlink() or (combined.exists() and combined.stat().st_uid != 0):
        raise ValueError("Неверный владелец ключа CA")
    if combined.exists():
        combined.chmod(0o600)
    valid = combined.is_file() and subprocess.run(
        ["openssl", "verify", "-CAfile", str(combined), str(combined)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0
    if not valid:
        with tempfile.TemporaryDirectory(dir=directory) as path:
            key = Path(path) / "key.pem"
            certificate = Path(path) / "certificate.pem"
            config = Path(path) / "req.conf"
            config.write_text("[req]\ndistinguished_name=dn\n[dn]\n")
            hostname = socket.gethostname().encode("idna").decode("ascii")
            hostname = "".join(ch for ch in hostname if ch.isalnum() or ch in "-.")[:40]
            name = "Digital Examen CA " + hostname
            if combined.exists():
                subprocess.run(["openssl", "pkey", "-in", str(combined), "-out", str(key)], check=True)
                key_options = ["-key", str(key)]
            else:
                key_options = ["-newkey", "rsa:3072", "-nodes", "-keyout", str(key)]
            subprocess.run([
                "openssl", "req", "-x509", "-config", str(config), *key_options, "-sha256",
                "-days", "1825", "-subj", "/CN=" + name + "/O=Digital Examen",
                "-addext", "basicConstraints=critical,CA:TRUE",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                "-out", str(certificate),
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            subprocess.run(["openssl", "verify", "-CAfile", str(certificate), str(certificate)],
                           stdout=subprocess.DEVNULL, check=True)
            write_file(combined, key.read_text() + certificate.read_text(), 0o600)
    combined.chmod(0o600)
    return subprocess.check_output(["openssl", "x509", "-in", str(combined), "-outform", "PEM"],
                                   text=True)


def chromium_ca(install):
    if not shutil.which("certutil"):
        return
    if install and not any(shutil.which(name) for name in
                           ("chromium", "chromium-browser", "google-chrome", "microsoft-edge")):
        return
    nickname = "Digital Examen Inspection CA"
    for entry in people() if install else pwd.getpwall():
        if entry.pw_uid == 0 or not entry.pw_dir.startswith("/home/"):
            continue
        home = Path(entry.pw_dir)
        run = ["runuser", "-u", entry.pw_name, "--"]
        databases = [home / ".pki/nssdb", home / ".local/share/pki/nssdb"]
        existing = [
            path for path in databases
            if subprocess.run(run + ["test", "-f", str(path / "cert9.db")]).returncode == 0
        ]
        if install and not existing:
            path = databases[0]
            subprocess.run(run + ["mkdir", "-p", "--", str(path)], check=True)
            subprocess.run(run + ["certutil", "-N", "--empty-password", "-d", "sql:" + str(path)],
                           check=True)
            existing.append(path)
        for path in existing:
            command = run + ["certutil", "-d", "sql:" + str(path)]
            current = subprocess.run(command + ["-L", "-n", nickname, "-a"], text=True,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            if install and current.returncode == 0:
                cert = ssl.PEM_cert_to_DER_cert(current.stdout.strip())
                expected = ssl.PEM_cert_to_DER_cert(ANCHOR.read_text().strip())
                entries = subprocess.check_output(command + ["-L"], text=True)
                if cert == expected and any(line.split()[-1:] == ["C,,"]
                                            for line in entries.splitlines()
                                            if line.startswith(nickname + " ")):
                    continue
            if current.returncode == 0:
                subprocess.run(command + ["-D", "-n", nickname], check=True)
            if install:
                subprocess.run(command + ["-A", "-t", "C,,", "-n", nickname, "-i", str(ANCHOR)],
                               check=True)


def sync(repair=False):
    if os.geteuid() != 0:
        raise PermissionError("Настройка CA требует root")
    version = subprocess.check_output(["/opt/de-inspect/bin/mitmdump", "--version"], text=True)
    if not any(line.startswith("Python:") and line.split()[1].startswith("3.8.")
               for line in version.splitlines()):
        raise ValueError("mitmdump должен работать с Python 3.8")
    if not repair:
        print(version, end="")
    if repair and not (CA_DIR / "mitmproxy-ca.pem").is_file():
        subprocess.run(["systemctl", "--no-block", "restart", "de-inspect.service"], check=True)
        return
    certificate = provision(CA_DIR)
    public = CA_DIR / "mitmproxy-ca-cert.pem"
    if not public.exists() or public.read_text() != certificate:
        write_file(public, certificate, 0o600)
    if not ANCHOR.exists() or ANCHOR.read_text() != certificate:
        write_file(ANCHOR, certificate)
    trusted = subprocess.run(
        ["openssl", "verify", "-CAfile", "/etc/pki/tls/certs/ca-bundle.crt", str(ANCHOR)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if trusted.returncode:
        subprocess.run(["update-ca-trust", "extract"], check=True)
    inspection_ca(str(ANCHOR), True)
    chromium_ca(True)
    if not HOSTS.exists() or HOSTS.read_text() != "*\n":
        write_file(HOSTS, "*\n")


def wait_ready():
    for _ in range(15):
        try:
            with socket.create_connection(("127.0.0.1", 3130), timeout=1):
                return
        except OSError:
            time.sleep(1)
    sys.exit("HTTPS-инспектор не открыл локальный порт")


if __name__ == "__main__":
    if sys.argv[1:] == ["ready"]:
        wait_ready()
        sys.exit(0)
    if os.geteuid() != 0:
        sys.exit("Настройка CA требует root")
    with open("/run/de-inspect-ca.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if sys.argv[1:] == ["remove"]:
            chromium_ca(False)
            inspection_ca(str(ANCHOR), False)
        else:
            sync(repair=sys.argv[1:] == ["repair"])
