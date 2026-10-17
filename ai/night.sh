#!/bin/sh

while true; do
    HOUR=`date +%H`
    if [ "$HOUR" -ge 1 ] && [ "$HOUR" -lt 6 ]; then
        echo "[ai-night] разметка спорных страниц"
        /usr/bin/python3 /usr/local/bin/label.py
        echo "[ai-night] доучивание"
        /usr/bin/python3 /usr/local/bin/train.py
        sleep 3600
    else
        sleep 900
    fi
done
