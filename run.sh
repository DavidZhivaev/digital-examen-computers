#!/bin/sh

# Раскатка школьного компьютера одной командой:
#
#   sudo sh run.sh
#
# Скрипт рассчитан на чистую машину: сам доставит всё, чего не хватает,
# перенесёт репозиторий в /opt/digital-examen-computers, спросит ключ
# администратора, корпус, аудиторию и номер компьютера и перезагрузится.
# Ничего настраивать заранее не нужно.

set -e

HERE=`dirname "$0"`
HERE=`cd "$HERE" && pwd`

REPO_DIR=/opt/digital-examen-computers
REPO_URL=https://github.com/DavidZhivaev/digital-examen-computers.git

say() {
    echo "[run] $1"
}

fail() {
    echo "[run] ОШИБКА: $1" >&2
    exit 1
}

[ "`id -u`" = "0" ] || fail "запускайте от root: sudo sh run.sh"

say "проверяю и доставляю всё нужное"
sh "$HERE/bin/de-deps" || fail "не хватает обязательных программ, смотрите строки [deps] выше"

if [ "$HERE" != "$REPO_DIR" ]; then
    say "переношу репозиторий в $REPO_DIR"
    if [ -d "$REPO_DIR/.git" ]; then
        git -C "$REPO_DIR" remote set-url origin "$REPO_URL"
        git -C "$REPO_DIR" fetch --quiet origin main || true
        git -C "$REPO_DIR" reset --hard --quiet origin/main || true
    else
        rm -rf "$REPO_DIR"
        git clone --quiet "$REPO_URL" "$REPO_DIR" || fail "не смог склонировать $REPO_URL"
    fi
    chown -R root:root "$REPO_DIR"
    chmod -R go-w "$REPO_DIR"
    say "продолжаю из $REPO_DIR"
    exec /bin/sh "$REPO_DIR/run.sh" "$@"
fi

say "запускаю установку"
exec /bin/sh "$REPO_DIR/setup.sh" "$@"
