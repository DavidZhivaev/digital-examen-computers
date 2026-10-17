#!/bin/sh

while true; do
    HOUR=`date +%H`
    if [ "$HOUR" -ge 2 ] && [ "$HOUR" -lt 6 ]; then
        echo "[ai-night] доучивание"
        /usr/bin/python3 /usr/local/bin/train.py
        sleep 3600
    else
        sleep 900
    fi
done
