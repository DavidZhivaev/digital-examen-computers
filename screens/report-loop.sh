#!/bin/sh

PYTHON="${DE_SCREEN_PYTHON:-/usr/bin/python3}"
EVERY="${DE_REPORT_EVERY:-120}"

while true; do
    "$PYTHON" /usr/local/bin/report.py
    sleep "$EVERY"
done
