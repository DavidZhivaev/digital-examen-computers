"""Remove only the external proxy assignments installed by school Ansible."""
from pathlib import Path
import re

import de_common

LEGACY_PROXY = re.compile(
    r'^\s*(?:export\s+)?(?:http_proxy|https_proxy)\s*=\s*'
    r'(?:"http://squid\.1580\.ru:80"|\'http://squid\.1580\.ru:80\'|http://squid\.1580\.ru:80)\s*$',
    re.IGNORECASE,
)


def remove_legacy_proxy(path):
    path = Path(path)
    if path.is_symlink():
        return False
    try:
        before = path.read_text()
        details = path.stat()
    except FileNotFoundError:
        return False
    after = "".join(line for line in before.splitlines(keepends=True) if not LEGACY_PROXY.fullmatch(line))
    if after == before or path.stat() != details or path.read_text() != before:
        return False
    de_common.write_file(path, after, details.st_mode & 0o777)
    return True


def reconcile():
    for path in ("/etc/environment", "/etc/sysconfig/snapd"):
        try:
            if remove_legacy_proxy(path):
                de_common.log("de-net", "убраны старые настройки школьного прокси из " + path)
        except (OSError, UnicodeError) as error:
            de_common.log("de-net", "не удалось согласовать " + path + ": " + str(error))
