#!/bin/sh

set -e

HERE=`dirname "$0"`
HERE=`cd "$HERE" && pwd`

CONFIG_DIR=/etc/digital-examen
CONFIG_FILE=$CONFIG_DIR/computers.conf
STATE_DIR=/var/lib/digital-examen/users
BIN_DIR=/usr/local/sbin
PAM_FILE=/etc/pam.d/sddm
MARK_START="# digital-examen-computers begin"
MARK_END="# digital-examen-computers end"

say() {
    echo "[setup] $1"
}

fail() {
    echo "[setup] ОШИБКА: $1" >&2
    exit 1
}

[ "`id -u`" = "0" ] || fail "запускайте от root: sudo sh setup.sh"

for tool in /usr/sbin/useradd /usr/sbin/userdel /usr/sbin/chpasswd /usr/bin/python3 /usr/bin/getent; do
    [ -x "$tool" ] || fail "не найден $tool"
done

[ -f /lib64/security/pam_exec.so ] || [ -f /usr/lib64/security/pam_exec.so ] || fail "не найден pam_exec.so"
[ -f "$PAM_FILE" ] || fail "не найден $PAM_FILE"

say "создаю каталоги"
mkdir -p "$CONFIG_DIR"
mkdir -p "$STATE_DIR"
chmod 0755 "$CONFIG_DIR"
chmod 0700 "$STATE_DIR"

say "ставлю скрипты в $BIN_DIR"
for script in de-login-auth de-session de-cleanup de-netns-user de-proxy-relay de-enter-net de-box de-screen de-agent de-palette de-report; do
    install -m 0750 -o root -g root "$HERE/bin/$script" "$BIN_DIR/$script"
done
install -m 0755 -o root -g root "$HERE/bin/de-session-start" /usr/local/bin/de-session-start

if [ ! -f "$CONFIG_FILE" ]; then
    say "создаю $CONFIG_FILE"
    install -m 0640 -o root -g root "$HERE/config/computers.conf.example" "$CONFIG_FILE"
else
    say "конфиг уже есть, не перезаписываю"
fi

. "$CONFIG_FILE"
GROUP="${DE_USER_GROUP:-de-students}"

say "защищаю всех существующих пользователей"
EXISTING=`getent passwd | cut -d: -f1 | sort -u | tr '\n' ' '`
PROTECTED=`echo "$EXISTING" | sed 's/  */ /g; s/^ //; s/ $//'`
TMP_CONFIG=`mktemp`
grep -v '^DE_PROTECTED_USERS=' "$CONFIG_FILE" > "$TMP_CONFIG"
echo "DE_PROTECTED_USERS=\"$PROTECTED\"" >> "$TMP_CONFIG"
cat "$TMP_CONFIG" > "$CONFIG_FILE"
rm -f "$TMP_CONFIG"
chmod 0640 "$CONFIG_FILE"

say "создаю каталог базы песочницы"
mkdir -p "${DE_BOX_BASE:-/var/lib/digital-examen/box-base}"

say "создаю группу $GROUP"
getent group "$GROUP" >/dev/null 2>&1 || /usr/sbin/groupadd --system "$GROUP"

say "запрещаю ssh и cron для $GROUP"
mkdir -p /etc/ssh/sshd_config.d
echo "DenyGroups $GROUP" > /etc/ssh/sshd_config.d/90-digital-examen.conf
chmod 0644 /etc/ssh/sshd_config.d/90-digital-examen.conf
if [ -f /etc/cron.deny ]; then
    say "cron.deny уже существует, пользователи добавляются при входе"
fi

say "настраиваю экран входа"
mkdir -p /etc/sddm.conf.d
install -m 0644 -o root -g root "$HERE/sddm/90-digital-examen.conf" /etc/sddm.conf.d/90-digital-examen.conf

say "правлю $PAM_FILE"
if grep -q "$MARK_START" "$PAM_FILE"; then
    say "хук PAM уже стоит"
else
    cp -a "$PAM_FILE" "$PAM_FILE.before-digital-examen"
    TMP_PAM=`mktemp`
    awk -v start="$MARK_START" -v stop="$MARK_END" '
        /^auth[ \t]+substack[ \t]+system-auth/ && done != 1 {
            print start
            print "auth        [success=done default=ignore]        pam_exec.so expose_authtok quiet /usr/local/sbin/de-login-auth"
            print stop
            done = 1
        }
        { print }
    ' "$PAM_FILE" > "$TMP_PAM"

    grep -q "$MARK_START" "$TMP_PAM" || fail "не нашёл строку system-auth в $PAM_FILE"

    {
        echo "$MARK_START"
        echo "session     optional                              pam_exec.so quiet /usr/local/sbin/de-session"
        echo "$MARK_END"
    } >> "$TMP_PAM"

    cat "$TMP_PAM" > "$PAM_FILE"
    rm -f "$TMP_PAM"
    chmod 0644 "$PAM_FILE"
fi

say "создаю общий секрет для прокси"
SECRET_FILE="${DE_PROXY_SECRET_FILE:-/etc/digital-examen/proxy.secret}"
if [ ! -f "$SECRET_FILE" ]; then
    say "секрета нет, впишите его вручную в $SECRET_FILE"
    touch "$SECRET_FILE"
fi
chmod 0600 "$SECRET_FILE"
chown root:root "$SECRET_FILE"

say "ставлю сеанс со школьным прокси"
mkdir -p /usr/share/xsessions
install -m 0644 -o root -g root "$HERE/xsessions/00-de-student.desktop" /usr/share/xsessions/00-de-student.desktop

say "проверяю и ставлю правило sudo"
TMP_SUDO=`mktemp`
cat "$HERE/config/sudoers.de-students" > "$TMP_SUDO"
chmod 0440 "$TMP_SUDO"
if /usr/sbin/visudo -cqf "$TMP_SUDO"; then
    install -m 0440 -o root -g root "$TMP_SUDO" /etc/sudoers.d/de-students
    rm -f "$TMP_SUDO"
else
    rm -f "$TMP_SUDO"
    fail "правило sudo не прошло проверку, ничего не менял"
fi

say "запрещаю вход в текстовую консоль для $GROUP"
if ! grep -q "notingroup $GROUP" /etc/pam.d/login; then
    cp -a /etc/pam.d/login /etc/pam.d/login.before-digital-examen
    TMP_LOGIN=`mktemp`
    {
        echo "$MARK_START"
        echo "auth        required        pam_succeed_if.so user notingroup $GROUP quiet"
        echo "$MARK_END"
        cat /etc/pam.d/login
    } > "$TMP_LOGIN"
    cat "$TMP_LOGIN" > /etc/pam.d/login
    rm -f "$TMP_LOGIN"
    chmod 0644 /etc/pam.d/login
fi

say "ставлю службы"
for unit in de-cleanup.service de-screen.service de-agent.service; do
    install -m 0644 -o root -g root "$HERE/systemd/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable de-cleanup.service
systemctl enable de-screen.service
systemctl enable de-agent.service

say "проверяю, что есть всё для сетевого замка и песочницы"
[ -x /usr/bin/ip ] || fail "не найден ip"
[ -x /usr/sbin/nft ] || fail "не найден nft"
[ -x /usr/bin/setpriv ] || fail "не найден setpriv"
[ -x /usr/bin/rsync ] || fail "не найден rsync"
[ -x /usr/bin/systemd-run ] || fail "не найден systemd-run"
[ -x /usr/bin/systemd-nspawn ] || say "systemd-nspawn не найден, песочница de-box работать не будет"
[ -x /usr/bin/ffmpeg ] || say "ffmpeg не найден, запись экрана работать не будет"
command -v magick >/dev/null 2>&1 || say "magick не найден, запись экрана работать не будет"

say "проверяю конфиг sshd"
/usr/sbin/sshd -t || fail "sshd не принял конфиг"
systemctl reload sshd >/dev/null 2>&1 || true

say "готово"
say "защищённые пользователи: $PROTECTED"
say "впишите DE_API_URL и общий секрет прокси, затем перезагрузите компьютер"
say "секрет прокси: $SECRET_FILE (должен совпадать с ШС)"
