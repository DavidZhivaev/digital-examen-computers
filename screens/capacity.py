#!/usr/bin/python3

import sys

COMPUTERS = 1440
CORPUSES = 4
PER_CORPUS = COMPUTERS // CORPUSES
LINK_MBITS = 100
LINK_SHARE = 50
DISK_BUDGET_GB = 900
TRAFFIC_BUDGET_GB = 100

DAY_START = 9
DAY_END = 18
LESSON_MIN = 40
BREAK_MIN = 15
BREAK_STATIC_SHARE = 20

STORE_STEP = 2
WATCH_STEP = 2
LIVE_STEP = 1

MEASURE_SECONDS = 44.0

CALM_BYTES = {2: 26076, 4: 17796, 6: 11358, 8: 9968, 10: 8556}
BUSY_BYTES = {2: 59922, 4: 53384, 6: 47750, 8: 47516, 10: 45884}

KEYFRAME_BYTES = 30000
KEYFRAME_EVERY_SECONDS = 240

HD_PIXEL_RATIO = (1280 * 720) / float(1024 * 576)
HD_FULL_FRAME = 47000

WATCH_SHARE = 0.25

VISITS_PER_LESSON = 12
VISIT_BYTES = 60
SCHOOL_DAYS_PER_WEEK = 5

LIFECYCLE = [
    (1, 2, "день записи"),
    (13, 4, "до 2 недель"),
    (7, 6, "3-я неделя"),
    (7, 8, "4-я неделя"),
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


def stored_rate(step, busy_share, hd):
    share = BREAK_STATIC_SHARE / 100.0
    keys = KEYFRAME_BYTES / float(KEYFRAME_EVERY_SECONDS)
    scale = HD_PIXEL_RATIO if hd else 1.0
    lesson = (delta_rate(step, busy_share) + keys) * scale
    quiet = (delta_rate(step, 0.0) + keys) * scale
    return lesson, share * quiet + (1.0 - share) * lesson


def day_parts():
    total = (DAY_END - DAY_START) * 60
    cycle = LESSON_MIN + BREAK_MIN
    cycles = total // cycle
    lessons = cycles + 1 if total - cycles * cycle >= LESSON_MIN else cycles
    return lessons * LESSON_MIN * 60, cycles * BREAK_MIN * 60


def archive_day_bytes(step, busy_share, watch_share):
    """Байт архива за день. Пока на кабинет смотрят, архив пишется в 720p."""
    lesson_s, break_s = day_parts()
    plain_lesson, plain_break = stored_rate(step, busy_share, False)
    hd_lesson, _ = stored_rate(step, busy_share, True)

    watched_s = lesson_s * watch_share
    normal_s = lesson_s - watched_s
    return watched_s * hd_lesson + normal_s * plain_lesson + break_s * plain_break


def live_rate():
    """Целый кадр 720p для сайта. Уходит только пока смотрят."""
    return HD_FULL_FRAME * HD_PIXEL_RATIO / HD_PIXEL_RATIO


def say(line=""):
    sys.stdout.write(line + "\n")


def main():
    lesson_s, break_s = day_parts()
    seconds = lesson_s + break_s
    allowed = LINK_MBITS * LINK_SHARE / 100.0

    say("СХЕМА")
    say("  съёмка всегда 1280x720, тайл 16x16, изменения склеиваются в полосы")
    say("  никто не смотрит  - архив 1024x576 раз в %d с" % STORE_STEP)
    say("  открыт кабинет    - архив 1280x720 раз в %d с + целый кадр 720p" % WATCH_STEP)
    say("  открыт компьютер  - то же, целый кадр раз в %d с" % LIVE_STEP)
    say("  ночью 720p ужимается до 1024x576, дальше 4, 6, 8 с, через 4 недели удаление")
    say()
    say("ДЕНЬ: уроки %d мин, перемены %d мин, статичных на перемене %d%%" % (
        lesson_s // 60, break_s // 60, BREAK_STATIC_SHARE))
    say("ШКОЛА: %d компьютеров, %d корпуса по %d. Канал %d Мбит/с, можно %g Мбит/с." % (
        COMPUTERS, CORPUSES, PER_CORPUS, LINK_MBITS, allowed))
    say("Доля времени урока, когда на кабинет смотрят: %d%%" % int(WATCH_SHARE * 100))
    say()

    say("КАНАЛ НА ОДИН КОРПУС (%d компьютеров)" % PER_CORPUS)
    say("%-28s %10s %12s %12s %10s" % ("сценарий", "кбит/с", "корпус", "доля от", "запас"))
    say("%-28s %10s %12s %12s %10s" % ("", "на комп", "Мбит/с", "100 Мбит/с", ""))
    for name, busy in SCENARIOS.items():
        day = archive_day_bytes(STORE_STEP, busy, WATCH_SHARE)
        kbits = day / seconds * 8 / 1000.0
        corpus = kbits * PER_CORPUS / 1000.0
        share = corpus * 100.0 / LINK_MBITS
        mark = "есть" if corpus <= allowed else "ПРЕВЫШЕН"
        say("%-28s %10.1f %12.2f %11.1f%% %10s" % (name, kbits, corpus, share, mark))

    say()
    say("ЖИВОЙ ПРОСМОТР СВЕРХ ЭТОГО")
    one = live_rate() * 8 / 1000.0
    say("  один компьютер, кадр раз в 1 с: %.0f кбит/с" % one)
    say("  кабинет 18 машин, кадр раз в 2 с: %.2f Мбит/с" % (one / 2 * 18 / 1000.0))
    say("  кабинет 30 машин, кадр раз в 2 с: %.2f Мбит/с" % (one / 2 * 30 / 1000.0))
    say("  это добавляется только к тому корпусу, чей кабинет открыли")

    say()
    say("ДИСК: запись одного учебного дня всей школы по мере старения")
    say("%-28s %9s %9s %9s %9s" % ("сценарий", "2 с", "4 с", "6 с", "8 с"))
    for name, busy in SCENARIOS.items():
        row = []
        for step in (2, 4, 6, 8):
            value = archive_day_bytes(step, busy, WATCH_SHARE if step == 2 else 0.0)
            row.append("%8.1f" % (value * COMPUTERS / 1e9))
        say("%-28s %s  ГБ" % (name, " ".join(row)))

    say()
    say("ДИСК: сколько держится всего при сроке хранения в 4 недели")
    say("Записи появляются только в учебные дни, %d в неделю: это %d записанных дней" % (
        SCHOOL_DAYS_PER_WEEK, sum(days for days, step, label in LIFECYCLE)))
    say("%-28s %12s %14s %14s" % ("сценарий", "всего ГБ", "доля от 900", "вердикт"))
    for name, busy in SCENARIOS.items():
        total = 0.0
        for days, step, label in LIFECYCLE:
            watch = WATCH_SHARE if step == 2 else 0.0
            total += days * archive_day_bytes(step, busy, watch) * COMPUTERS / 1e9
        share = total * 100.0 / DISK_BUDGET_GB
        verdict = "влезает" if total <= DISK_BUDGET_GB else "лишние дни удалятся"
        say("%-28s %10.0f %13.0f%% %18s" % (name, total, share, verdict))

    say()
    say("Из чего складывается (сценарий «обычная работа»)")
    busy = SCENARIOS["обычная работа"]
    running = 0.0
    for days, step, label in LIFECYCLE:
        watch = WATCH_SHARE if step == 2 else 0.0
        part = days * archive_day_bytes(step, watch and busy or busy, watch) * COMPUTERS / 1e9
        running += part
        say("  %-16s %2d дней по %d с = %6.0f ГБ   накопительно %6.0f ГБ" % (
            label, days, step, part, running))

    say()
    say("ЖУРНАЛ ПОСЕЩЕНИЙ (бюджет %d ГБ)" % TRAFFIC_BUDGET_GB)
    lessons = lesson_s / 60 // LESSON_MIN
    per_day = VISITS_PER_LESSON * lessons * VISIT_BYTES * COMPUTERS
    say("  %d записей за урок по %d байт, %d уроков: %.2f ГБ в день на школу" % (
        VISITS_PER_LESSON, VISIT_BYTES, lessons, per_day / 1e9))
    say("  за учебный год (%d дней): %.0f ГБ" % (
        SCHOOL_DAYS_PER_WEEK * 35, per_day * SCHOOL_DAYS_PER_WEEK * 35 / 1e9))
    say("  бюджета хватает на %.0f учебных дней" % (TRAFFIC_BUDGET_GB * 1e9 / per_day))

    return 0


sys.exit(main())
