#!/bin/sh

DATA_DIR="${DE_SCREEN_DATA:-/data/digital-examen/screens}"
KEEP_DAYS="${DE_SCREEN_KEEP_DAYS:-10}"

[ -d "$DATA_DIR" ] || exit 0

LIMIT=`date -d "-$KEEP_DAYS days" +%Y-%m-%d`

for path in "$DATA_DIR"/*; do
    [ -d "$path" ] || continue
    day=`basename "$path"`
    case "$day" in
        [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
        *) continue ;;
    esac
    if [ "$day" \< "$LIMIT" ]; then
        rm -rf "$path"
        echo "removed $day"
    fi
done
