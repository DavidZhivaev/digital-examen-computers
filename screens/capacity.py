#!/usr/bin/python3

import sys

COMPUTERS = 1440
PER_CORPUS = 360
LINK_MBITS = 100
LINK_SHARE = 50
DISK_BUDGET_GB = 900

DAY_START = 9
DAY_END = 18
LESSON_MIN = 40
BREAK_MIN = 15
BREAK_STATIC_SHARE = 20

SEND_STEP = 2
LIVE_STEP = 1

MEASURE_SECONDS = 44.0

CALM_BYTES = {2: 26076, 4: 17796, 6: 11358, 8: 9968, 10: 8556}
BUSY_BYTES = {2: 59922, 4: 53384, 6: 47750, 8: 47516, 10: 45884}

LIVE_FRAME_CALM = 1078
LIVE_FRAME_BUSY = 2329

KEYFRAME_BYTES = 30000
KEYFRAME_EVERY_SECONDS = 240

SCHOOL_DAYS_PER_WEEK = 5

LIFECYCLE = [
    (1, 2, "в день записи"),
    (9, 4, "до 2 недель"),
    (5, 6, "3-я неделя"),
    (5, 8, "4-я неделя"),
    (10, 10, "5-я и 6-я недели"),
]

SCENARIOS = {
    "поток текста без остановки": 1.00,
    "обычная работа": 0.30,
    "чтение и редкие правки": 0.10,
    "почти статика": 0.02,
}


def delta_rate(step, busy_share):
    calm = CALM_BYTES[step] / MEASURE_SECONDS
    busy = BUSY_BYTES[step] / MEASURE_SECONDS
    return busy_share * busy + (1.0 - busy_share) * calm


def stored_rate(step, busy_share):
    share = BREAK_STATIC_SHARE / 100.0
    keys = KEYFRAME_BYTES / float(KEYFRAME_EVERY_SECONDS)
    lesson = delta_rate(step, busy_share) + keys
    quiet = delta_rate(step, 0.0) + keys
    return lesson, share * quiet + (1.0 - share) * lesson


def day_parts():
    total = (DAY_END - DAY_START) * 60
    cycle = LESSON_MIN + BREAK_MIN
    cycles = total // cycle
    lessons = cycles + 1 if total - cycles * cycle >= LESSON_MIN else cycles
    return lessons * LESSON_MIN * 60, cycles * BREAK_MIN * 60


def day_bytes(step, busy_share):
    lesson_s, break_s = day_parts()
    lesson, during_break = stored_rate(step, busy_share)
    return lesson_s * lesson + break_s * during_break


def live_rate(busy_share):
    calm = LIVE_FRAME_CALM / float(LIVE_STEP)
    busy = LIVE_FRAME_BUSY / float(LIVE_STEP)
    return busy_share * busy + (1.0 - busy_share) * calm


def main():
    lesson_s, break_s = day_parts()
    seconds = lesson_s + break_s
    allowed = LINK_MBITS * LINK_SHARE / 100.0

    print("1024x576, тайл 16x16, изменения склеиваются в полосы")
    print("По сети: раз в %d с, у кого открыт экран учителя: раз в %d с" % (SEND_STEP, LIVE_STEP))
    print("На диск: раз в %d с, ночью урезается до 4 с, дальше 6, 8, 10 с, через 6 недель удаляется" % SEND_STEP)
    print("День: уроки %d мин, перемены %d мин, статичных на перемене %d%%" % (
        lesson_s // 60, break_s // 60, BREAK_STATIC_SHARE))
    print("Компьютеров %d, в корпусе %d. Канал %d Мбит/с, можно %g. Диск %d ГБ." % (
        COMPUTERS, PER_CORPUS, LINK_MBITS, allowed, DISK_BUDGET_GB))
    print()

    print("КАНАЛ")
    print("%-28s %10s %12s %14s" % ("сценарий", "кбит/с", "корпус", "живой просмотр"))
    print("%-28s %10s %12s %14s" % ("", "на комп", "Мбит/с", "один комп"))
    for name, busy in SCENARIOS.items():
        day = day_bytes(SEND_STEP, busy)
        kbits = day / seconds * 8 / 1000.0
        corpus = kbits * PER_CORPUS / 1000.0
        live = live_rate(busy) * 8 / 1000.0
        mark = "" if corpus <= allowed else "  ПРЕВЫШЕН"
        print("%-28s %10.1f %12.2f %11.1f кбит/с%s" % (name, kbits, corpus, live, mark))

    print()
    print("ДИСК: сколько занимает запись одного учебного дня по мере старения")
    print("%-28s %9s %9s %9s %9s %9s" % ("сценарий", "2 с", "4 с", "6 с", "8 с", "10 с"))
    for name, busy in SCENARIOS.items():
        row = ["%8.1f" % (day_bytes(step, busy) * COMPUTERS / 1e9) for step in (2, 4, 6, 8, 10)]
        print("%-28s %s" % (name, " ".join(row)) + "  ГБ на всю школу")

    print()
    print("ДИСК: сколько держится всего при вашем жизненном цикле")
    print("Записи появляются только в учебные дни, %d в неделю, поэтому 6 недель это %d записей" % (
        SCHOOL_DAYS_PER_WEEK, sum(days for days, step, label in LIFECYCLE)))
    print("%-28s %14s %16s %12s" % ("сценарий", "всего ГБ", "доля от 900 ГБ", "вердикт"))
    for name, busy in SCENARIOS.items():
        total = 0.0
        for days, step, label in LIFECYCLE:
            total += days * day_bytes(step, busy) * COMPUTERS / 1e9
        share = total * 100.0 / DISK_BUDGET_GB
        verdict = "влезает" if total <= DISK_BUDGET_GB else "НЕ ВЛЕЗАЕТ"
        print("%-28s %12.0f %14.0f%% %14s" % (name, total, share, verdict))

    print()
    print("Из чего складывается (сценарий «обычная работа»)")
    busy = SCENARIOS["обычная работа"]
    running = 0.0
    for days, step, label in LIFECYCLE:
        part = days * day_bytes(step, busy) * COMPUTERS / 1e9
        running += part
        print("  %-20s %2d дней по %2d с = %6.0f ГБ   накопительно %6.0f ГБ" % (
            label, days, step, part, running))

    return 0


sys.exit(main())
