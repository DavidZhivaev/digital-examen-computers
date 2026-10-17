#!/usr/bin/python3

import sys

COMPUTERS = 1440
PER_CORPUS = 360
LINK_MBITS = 100
LINK_SHARE = 50
DISK_GB = 2000
DISK_BUDGET_GB = 900

DAY_START = 9
DAY_END = 18
LESSON_MIN = 40
BREAK_MIN = 15
BREAK_STATIC_SHARE = 20

NETWORK_STEP = 3
STORE_STEP = 3
LIVE_STEP = 1

BUSY_FRAME = {1: 2329, 2: 2806, 3: 3108, 4: 3687, 6: 3686, 8: 6619, 12: 6899, 16: 10104, 24: 18627}
CALM_FRAME = {1: 1078, 2: 1169, 3: 991, 4: 1189, 6: 874, 8: 1176, 12: 937, 16: 1253, 24: 1041}

SCENARIOS = {
    "поток текста без остановки": 1.00,
    "обычная работа": 0.30,
    "чтение и редкие правки": 0.10,
    "почти статика": 0.02,
}


def frame_bytes(table, step):
    if step in table:
        return table[step]
    keys = sorted(table)
    low = max([k for k in keys if k <= step] or [keys[0]])
    high = min([k for k in keys if k >= step] or [keys[-1]])
    if low == high:
        return table[low]
    part = (step - low) / float(high - low)
    return table[low] + (table[high] - table[low]) * part


def rate(step, busy_share):
    busy = frame_bytes(BUSY_FRAME, step)
    calm = frame_bytes(CALM_FRAME, step)
    return (busy_share * busy + (1.0 - busy_share) * calm) / float(step)


def day_parts():
    total = (DAY_END - DAY_START) * 60
    cycle = LESSON_MIN + BREAK_MIN
    cycles = total // cycle
    lessons = cycles + 1 if total - cycles * cycle >= LESSON_MIN else cycles
    return lessons * LESSON_MIN * 60, cycles * BREAK_MIN * 60


def model(busy_share):
    lesson_s, break_s = day_parts()
    seconds = lesson_s + break_s

    share = BREAK_STATIC_SHARE / 100.0

    net_lesson = rate(NETWORK_STEP, busy_share)
    net_break = share * rate(NETWORK_STEP, 0.0) + (1.0 - share) * rate(NETWORK_STEP, busy_share)
    net_bytes = lesson_s * net_lesson + break_s * net_break

    store_lesson = rate(STORE_STEP, busy_share)
    store_break = share * rate(STORE_STEP, 0.0) + (1.0 - share) * rate(STORE_STEP, busy_share)
    store_bytes = lesson_s * store_lesson + break_s * store_break

    return {
        "kbits": net_bytes / seconds * 8 / 1000.0,
        "peak_kbits": net_lesson * 8 / 1000.0,
        "mb_day": store_bytes / 1e6,
        "corpus_mbits": net_bytes / seconds * 8 * PER_CORPUS / 1e6,
        "school_gb_day": store_bytes * COMPUTERS / 1e9,
    }


def days_fit(result):
    per_day = result["school_gb_day"]
    if per_day <= 0:
        return 0
    return int(DISK_BUDGET_GB / per_day)


def main():
    lesson_s, break_s = day_parts()
    allowed = LINK_MBITS * LINK_SHARE / 100.0

    print("1024x576, тайл 16x16, изменения склеиваются в полосы")
    print("По сети: кадр раз в %d с. На диск: кадр раз в %d с. Живой просмотр: раз в %d с." % (
        NETWORK_STEP, STORE_STEP, LIVE_STEP))
    print("День: уроки %d мин, перемены %d мин, статичных на перемене %d%%" % (
        lesson_s // 60, break_s // 60, BREAK_STATIC_SHARE))
    print("Компьютеров %d, в корпусе %d. Канал %d Мбит/с, можно %g. Диск %d ГБ." % (
        COMPUTERS, PER_CORPUS, LINK_MBITS, allowed, DISK_BUDGET_GB))
    print()
    print("%-28s %8s %8s %9s %10s %10s" % (
        "сценарий", "кбит/с", "пик", "МБ/день", "корпус", "школа"))
    print("%-28s %8s %8s %9s %10s %10s" % (
        "", "средн.", "урок", "на диск", "Мбит/с", "ГБ/день"))

    results = {}
    for name, busy in SCENARIOS.items():
        item = model(busy)
        results[name] = item
        mark = "" if item["corpus_mbits"] <= allowed else "  ПРЕВЫШЕН КАНАЛ"
        print("%-28s %8.1f %8.1f %9.1f %10.2f %10.1f%s" % (
            name, item["kbits"], item["peak_kbits"], item["mb_day"],
            item["corpus_mbits"], item["school_gb_day"], mark))

    print()
    print("Сколько дней помещается в %d ГБ" % DISK_BUDGET_GB)
    print("%-28s %12s %12s %14s" % ("сценарий", "10 дней", "30 дней", "влезает дней"))
    for name in SCENARIOS:
        item = results[name]
        print("%-28s %10.0f ГБ %10.0f ГБ %12d" % (
            name, item["school_gb_day"] * 10, item["school_gb_day"] * 30, days_fit(item)))

    print()
    print("Живой просмотр учителем, кадр раз в %d с:" % LIVE_STEP)
    for name, busy in SCENARIOS.items():
        one = rate(LIVE_STEP, busy) * 8 / 1000.0
        print("  %-28s один комп %6.1f кбит/с   кабинет 18 машин %6.2f Мбит/с   корпус 360 машин %6.1f Мбит/с" % (
            name, one, one * 18 / 1000.0, one * 360 / 1000.0))

    return 0


sys.exit(main())
