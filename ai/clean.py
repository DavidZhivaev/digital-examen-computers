#!/usr/bin/python3

"""Выбросить придуманные страницы из выборки.

В выборке лежат сто придуманных страниц с пометкой `"by":
"синтетика-проверка"`. Они проверяли, что обучение вообще работает, но
качество по ним считать нельзя: модель, выученная на придуманном, на живых
страницах ведёт себя иначе.

Запускать, когда настоящих страниц наберётся хотя бы сорок:

    python3 clean.py                 посмотреть, сколько чего
    python3 clean.py --выбросить     переписать файл без придуманных
"""

import json
import os
import shutil
import sys
import time

DATA_FILE = os.environ.get("DE_AI_DATA", "/data/digital-examen/ai/labelled.jsonl")
MARK = "синтетика"
NEED_REAL = int(os.environ.get("DE_AI_NEED_REAL", "40"))


def read(path):
    rows = []
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def fake(row):
    return MARK in str(row.get("by", ""))


def main():
    path = sys.argv[-1] if sys.argv[-1].endswith(".jsonl") else DATA_FILE
    if not os.path.exists(path):
        sys.stdout.write("нет выборки: %s\n" % path)
        return 1

    rows = read(path)
    fakes = [row for row in rows if fake(row)]
    real = [row for row in rows if not fake(row)]

    sys.stdout.write("всего строк: %d\n" % len(rows))
    sys.stdout.write("придуманных: %d\n" % len(fakes))
    sys.stdout.write("настоящих  : %d (из них «ИИ» %d)\n"
                     % (len(real), sum(1 for row in real if row.get("label"))))

    if "--выбросить" not in sys.argv:
        sys.stdout.write("\nничего не изменено. Чтобы переписать файл: --выбросить\n")
        return 0

    if len(real) < NEED_REAL:
        sys.stdout.write("\nнастоящих всего %d, нужно хотя бы %d - выбрасывать рано\n"
                         % (len(real), NEED_REAL))
        return 1

    backup = path + ".bak-" + time.strftime("%Y%m%dT%H%M%S")
    shutil.copy2(path, backup)

    out = open(path, "w")
    for row in real:
        out.write(json.dumps(row, ensure_ascii=False) + "\n")
    out.close()

    sys.stdout.write("\nоставлено %d настоящих, прежний файл: %s\n" % (len(real), backup))
    sys.stdout.write("теперь переучить: python3 train.py\n")
    return 0


sys.exit(main())
