"""Шаблоны адресов: одни и те же правила у фильтра и у ограничений.

Понимаем четыре вида строк:

    ya.ru        ровно этот узел
    *.ya.ru      этот узел и всё, что под ним
    10.0.0.7     адрес
    10.0.0.0/24  сеть
    *            что угодно

Строка, начатая с «!», — исключение: она отменяет попадание остальных строк
того же списка. Так закрывают `*.google.com`, оставляя открытым
`maps.google.com`.
"""

import ipaddress
import os


def read_list(path):
    """Список шаблонов из файла. Пустые строки и «#» пропускаем."""
    if not path or not os.path.exists(path):
        return []
    lines = []
    try:
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#"):
                lines.append(line)
    except IOError:
        return []
    return lines


def host_matches(pattern, host):
    pattern = pattern.strip().lower()
    host = host.strip().lower().rstrip(".")
    if not pattern or not host:
        return False
    if pattern == "*":
        return True
    if pattern.startswith("*."):
        tail = pattern[2:]
        return host == tail or host.endswith("." + tail)
    if pattern.startswith("."):
        tail = pattern[1:]
        return host == tail or host.endswith("." + tail)
    return host == pattern


def address_matches(pattern, host):
    try:
        network = ipaddress.ip_network(pattern, strict=False)
    except ValueError:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address in network


def matches(pattern, host):
    if "/" in pattern:
        return address_matches(pattern, host)
    if address_matches(pattern, host):
        return True
    return host_matches(pattern, host)


def any_match(lines, host):
    """Попал ли узел в список, с учётом строк-исключений."""
    plain = []
    exceptions = []
    for line in lines or []:
        line = str(line).strip()
        if not line:
            continue
        if line.startswith("!"):
            exceptions.append(line[1:].strip())
        else:
            plain.append(line)

    for pattern in exceptions:
        if matches(pattern, host):
            return False
    for pattern in plain:
        if matches(pattern, host):
            return True
    return False
