"""Присмотр за поведением соединений: туннели и переписка с ИИ.

Запреты по именам узлов ловят известное. Туннель и чат с незнакомой
моделью известными не бывают: ученик поднимает свой сервер на арендованном
адресе, и в списке его нет. Поэтому здесь смотрим не на имя, а на то, как
соединение себя ведёт.

Что считается подозрительным:

- **туннель**: одно и то же соединение живёт долго, байты идут в обе
  стороны ровным потоком, а страницы по нему ни разу не было. Так ведёт
  себя VPN поверх TLS, но не ведёт браузер;
- **необычный порт**: CONNECT не на 443. Нормальному сайту другой порт не
  нужен, а туннелю — сплошь и рядом;
- **голый адрес**: CONNECT на IP без имени. Браузер почти всегда ходит по
  имени;
- **переписка с моделью**: много мелких запросов к одному узлу и длинные
  ответы, которые текут (`text/event-stream`). Так выглядит чат с ИИ, на
  каком бы домене он ни стоял.

Ничего не блокируем: это наблюдение, а не запрет. Решение принимает
человек на сайте, а он видит ровно те признаки, по которым мы сработали.
Ложное срабатывание здесь дешевле пропуска, но блокировать по нему нельзя:
за «ровным потоком в обе стороны» стоит и видеозвонок, и игра.
"""

import threading
import time

LOCK = threading.Lock()
HOSTS = {}

LONG_SECONDS = 180
STEADY_BYTES = 2 * 1024 * 1024
CHAT_REQUESTS = 12
QUIET_SECONDS = 1800


def is_address(host):
    parts = str(host).split(".")
    return len(parts) == 4 and all(part.isdigit() for part in parts)


def note(host, login, port=443, up=0, down=0, seconds=0.0,
         pages=0, requests=0, streams=0):
    """Приложить к счёту узла то, что только что произошло."""
    if not host:
        return
    key = (login, host)
    now = time.time()
    with LOCK:
        row = HOSTS.get(key)
        if row is None:
            row = {
                "host": host, "login": login, "port": port,
                "up": 0, "down": 0, "seconds": 0.0,
                "pages": 0, "requests": 0, "streams": 0,
                "first": now, "told": "",
            }
            HOSTS[key] = row
        row["port"] = port or row["port"]
        row["up"] += up
        row["down"] += down
        row["seconds"] += seconds
        row["pages"] += pages
        row["requests"] += requests
        row["streams"] += streams
        row["last"] = now
        return dict(row)


def reasons(row):
    """Чем именно этот узел подозрителен. Пусто — ничем."""
    found = []

    if row["port"] and int(row["port"]) != 443:
        found.append("порт %s вместо 443" % row["port"])

    if is_address(row["host"]):
        found.append("соединение по адресу, а не по имени")

    both_ways = row["up"] > 131072 and row["down"] > 131072
    if row["seconds"] >= LONG_SECONDS and both_ways and row["pages"] == 0:
        found.append("долгий поток в обе стороны без единой страницы")

    if row["up"] + row["down"] >= STEADY_BYTES and row["pages"] == 0 and row["requests"] <= 2:
        found.append("много байт в одном соединении без запросов страниц")

    if row["streams"] >= 3 and row["requests"] >= CHAT_REQUESTS:
        found.append("похоже на переписку с моделью: много мелких запросов и текущие ответы")

    return found


def check(row):
    """(о чём сообщить, признаки) — или (пусто, пусто), если всё обычно.

    Об одном и том же узле сообщаем один раз: повторять каждую минуту
    бессмысленно, а журнал раздувается.
    """
    if not row:
        return "", []
    found = reasons(row)
    if not found:
        return "", []

    key = (row["login"], row["host"])
    mark = "|".join(sorted(found))
    with LOCK:
        kept = HOSTS.get(key)
        if kept is None or kept.get("told") == mark:
            return "", []
        kept["told"] = mark

    return row["host"], found


def forget_old(now=None):
    """Выбросить узлы, о которых давно ничего не слышно."""
    now = now or time.time()
    with LOCK:
        for key in [key for key, row in HOSTS.items()
                    if now - row.get("last", row["first"]) > QUIET_SECONDS]:
            HOSTS.pop(key, None)
