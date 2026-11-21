import os
from pathlib import Path
import re
import subprocess

STATE = Path("/var/lib/digital-examen/installed-script")


def scripts(repo):
    numbered = []
    for path in (Path(repo) / "scripts").glob("*.sh"):
        if path.is_file() and not path.is_symlink() and re.fullmatch(r"[1-9][0-9]*", path.stem):
            numbered.append((int(path.stem), path))
    return sorted(numbered)


def save(number):
    temporary = STATE.with_suffix(".new")
    temporary.write_text(str(number) + "\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, STATE)


def initialize(repo):
    if not STATE.exists():
        save(max((number for number, _ in scripts(repo)), default=0))


def apply(repo):
    if not STATE.exists():
        initialize(repo)
        return
    last = int(STATE.read_text().strip())
    for number, path in scripts(repo):
        if number <= last:
            continue
        subprocess.run(["/bin/sh", str(path)], cwd=repo, timeout=300, check=True)
        save(number)
