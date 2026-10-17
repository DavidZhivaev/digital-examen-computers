#!/bin/sh

PYTHON="${DE_SCREEN_PYTHON:-/usr/bin/python3}"
START="${DE_NIGHT_START:-22}"
STOP="${DE_NIGHT_STOP:-7}"

while true; do
    hour=`date +%H`
    hour=`expr "$hour" + 0`
    if [ "$hour" -ge "$START" ] || [ "$hour" -lt "$STOP" ]; then
        nice -n 19 "$PYTHON" /usr/local/bin/lifecycle.py
        sleep 3600
    else
        sleep 600
    fi
done
