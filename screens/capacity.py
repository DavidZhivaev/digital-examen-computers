#!/usr/bin/python3

import sys

COMPUTERS = 1440
PER_CORPUS = 360
LINK_MBITS = 100
LINK_SHARE = 50
DISK_GB = 2000
DISK_SHARE = 40

DAY_START = 9
DAY_END = 18
LESSON_MIN = 40
BREAK_MIN = 15
BREAK_STATIC_SHARE = 20

KEYFRAME_BYTES = 47000
BUSY_FRAME_BYTES = 51700
CALM_FRAME_BYTES = 2600
KEYFRAME_EVERY_SECONDS = 480

QUALITY = {
    "1280x720 q50": 1.00,
    "1280x720 q35": 0.88,
    "1280x720 q25": 0.80,
    "1024x576 q35": 0.57,
    "960x540 q35": 0.53,
    "960x540 q25": 0.50,
    "800x450 q35": 0.38,
    "800x450 q25": 0.36,
}

SCENARIOS = {
    "поток текста без остановки": 0.73,
    "обычная работа": 0.25,
    "чтение и редкие правки": 0.10,
    "почти статика": 0.02,
}

THIN_GAIN = 1.8


def day_seconds():
    total = (DAY_END - DAY_START) * 60
    cycle = LESSON_MIN + BREAK_MIN
    cycles = total // cycle
    lessons = cycles + 1 if total - cycles * cycle >= LESSON_MIN else cycles
    breaks = cycles
    return lessons * LESSON_MIN * 60, breaks * BREAK_MIN * 60


def frame_rate(busy_share, cadence, factor):
    busy = BUSY_FRAME_BYTES * factor
    calm = CALM_FRAME_BYTES * factor
    per_frame = busy_share * busy + (1.0 - busy_share) * calm
    keyframes = KEYFRAME_BYTES * factor / KEYFRAME_EVERY_SECONDS
    return per_frame / cadence + keyframes


def model(busy_share, cadence, factor):
    lesson_s, break_s = day_seconds()
    active = frame_rate(busy_share, cadence, factor)
    static = frame_rate(0.0, cadence, factor)

    share = BREAK_STATIC_SHARE / 100.0
    during_break = share * static + (1.0 - share) * active

    total_s = lesson_s + break_s
    average = (lesson_s * active + break_s * during_break) / total_s

    return {
        "bytes_per_second": average,
        "kbits": average * 8 / 1000.0,
        "mb_per_day": average * total_s / 1e6,
        "corpus_mbits": average * 8 * PER_CORPUS / 1e6,
        "school_gb_per_day": average * total_s * COMPUTERS / 1e9,
        "seconds": total_s,
    }


def retention(gb_per_day, days_fast, days_slow):
    return gb_per_day * (days_fast + days_slow / THIN_GAIN)


def main():
    cadence = float(sys.argv[1]) if len(sys.argv) > 1 else 4.0
    quality = sys.argv[2] if len(sys.argv) > 2 else "1280x720 q50"
    factor = QUALITY.get(quality)
    if factor is None:
        sys.stderr.write("нет такого качества\n")
        return 1

    lesson_s, break_s = day_seconds()
    budget_gb = DISK_GB * DISK_SHARE / 100.0
    allowed_mbits = LINK_MBITS * LINK_SHARE / 100.0

    print("Компьютеров в школе: %d, в корпусе: %d" % (COMPUTERS, PER_CORPUS))
    print("Учебный день: уроки %d мин, перемены %d мин, всего %d ч" % (
        lesson_s // 60, break_s // 60, (lesson_s + break_s) // 3600))
    print("На перемене статичных компьютеров: %d%%" % BREAK_STATIC_SHARE)
    print("Кадр раз в %g с, качество %s" % (cadence, quality))
    print("Канал корпуса: %d Мбит/с, можно занять %g Мбит/с" % (LINK_MBITS, allowed_mbits))
    print("Диск: %d ГБ, можно занять %g ГБ" % (DISK_GB, budget_gb))
    print()
    print("%-28s %9s %9s %11s %13s" % (
        "сценарий", "кбит/с", "МБ/день", "корпус", "вся школа"))
    print("%-28s %9s %9s %11s %13s" % ("", "на комп", "на комп", "Мбит/с", "ГБ/день"))

    for name, busy in SCENARIOS.items():
        result = model(busy, cadence, factor)
        mark = "" if result["corpus_mbits"] <= allowed_mbits else "  ПРЕВЫШЕН КАНАЛ"
        print("%-28s %9.1f %9.1f %11.1f %13.1f%s" % (
            name, result["kbits"], result["mb_per_day"],
            result["corpus_mbits"], result["school_gb_per_day"], mark))

    print()
    print("Хранение 14 дней в x%g плюс 16 дней в x%g:" % (cadence, cadence * 2))
    for name, busy in SCENARIOS.items():
        result = model(busy, cadence, factor)
        need = retention(result["school_gb_per_day"], 14, 16)
        ten = result["school_gb_per_day"] * 10
        mark = "влезает" if need <= budget_gb else "НЕ ВЛЕЗАЕТ, нужно %.0f%% диска" % (
            need * 100.0 / DISK_GB)
        print("  %-28s 10 дней %7.0f ГБ   14+16 дней %7.0f ГБ   %s" % (name, ten, need, mark))

    return 0


sys.exit(main())
