"""Remove only the external proxy assignments installed by school Ansible."""
from pathlib import Path
import re

import de_common

LEGACY_PROXY = re.compile(
    r'''^\s*(?:export\s+)?(?:http_proxy|https_proxy|all_proxy)\s*=\s*'''
    r'''(["']?)(?:http://)?(?:squid\.1580\.ru:80|10\.171\.219\.221:3128)\1\s*$''',
    re.IGNORECASE,
)

LEGACY_NO_PROXY = (
    'localhost,127.0.0.1,10.0.0.0/8,172.0.0.0/8'
    ',bf726e13342a.hosting.myjino.ru,downloads.arduino.cc,login.mos.ru,mos.ru'
    ',online.olimpiada.ru,repo.os.mos.ru,school.mos.ru,sdo.1580.ru'
    ',silvertests.ru,spt.gppc.ru,tinkercad.com,uchebnik.mos.ru'
    ',www.tinkercad.com'
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
    before_lines = before.splitlines(keepends=True)
    after = "".join(
        line for line in before_lines
        if not LEGACY_PROXY.fullmatch(line) and line.strip() != f'no_proxy="{LEGACY_NO_PROXY}"'
    )
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
