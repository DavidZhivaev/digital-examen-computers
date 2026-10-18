"""Урок сейчас или перемена.

Отвечает по расписанию звонков, которое приходит с сайта вместе с
политикой кабинета и лежит в `policy.json`. Это не расписание классов: нам
нужно только знать, идёт ли урок, и звонки отвечают на это полностью.

Если расписания нет совсем, считаем, что урок идёт: пропустить запись из-за
отсутствующего расписания хуже, чем записать лишнее.
"""

import json
import os
import time


def read_bells(path="/run/digital-examen/policy.json"):
    if not os.path.exists(path):
        return []
    try:
        policy = json.loads(open(path).read())
    except (ValueError, IOError):
        return []
    return policy.get("bells") or []


def minutes(clock):
    parts = str(clock).split(":")
    if len(parts) != 2:
        return -1
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        return -1


def lesson_now(bells=None, when=None):
    """(идёт ли урок, номер урока). Номер — 0, если урока нет."""
    if bells is None:
        bells = read_bells()
    if not bells:
        return True, 0

    moment = time.localtime(when if when is not None else time.time())
    weekday = str(moment.tm_wday + 1)
    now = moment.tm_hour * 60 + moment.tm_min

    for bell in bells:
        days = str(bell.get("weekdays", "12345"))
        if days and weekday not in days:
            continue
        starts = minutes(bell.get("starts", ""))
        ends = minutes(bell.get("ends", ""))
        if starts < 0 or ends < 0:
            continue
        if starts <= now < ends:
            return True, int(bell.get("number", 0))

    return False, 0


def school_day(bells=None, when=None):
    """Учебный ли сейчас день по расписанию звонков."""
    if bells is None:
        bells = read_bells()
    if not bells:
        return True
    moment = time.localtime(when if when is not None else time.time())
    weekday = str(moment.tm_wday + 1)
    for bell in bells:
        days = str(bell.get("weekdays", "12345"))
        if not days or weekday in days:
            return True
    return False
