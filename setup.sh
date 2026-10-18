#!/bin/sh

set -e

HERE=`dirname "$0"`
HERE=`cd "$HERE" && pwd`

MODE=install
[ "$1" = "--update" ] && MODE=update

REPO_DIR=/opt/digital-examen-computers
REPO_URL=https://github.com/DavidZhivaev/digital-examen-computers.git

CONFIG_DIR=/etc/digital-examen
CONFIG_FILE=$CONFIG_DIR/computers.conf
STATE_DIR=/var/lib/digital-examen/users
BIN_DIR=/usr/local/sbin
LIB_DIR=/usr/local/lib/digital-examen
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

say "проверяю и доставляю всё нужное"
sh "$HERE/bin/de-deps" || fail "не хватает обязательных программ, смотрите строки [deps] выше"

for tool in useradd userdel chpasswd python3 getent; do
    command -v "$tool" >/dev/null 2>&1 || [ -x "/usr/sbin/$tool" ] || fail "не найден $tool"
done

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
    exec /bin/sh "$REPO_DIR/setup.sh" "$@"
fi

[ -f /lib64/security/pam_exec.so ] || [ -f /usr/lib64/security/pam_exec.so ] || fail "не найден pam_exec.so"
[ -f "$PAM_FILE" ] || fail "не найден $PAM_FILE"

say "создаю каталоги"
mkdir -p "$CONFIG_DIR"
mkdir -p "$STATE_DIR"
chmod 0755 "$CONFIG_DIR"
chmod 0700 "$STATE_DIR"

say "ставлю общие модули в $LIB_DIR"
mkdir -p "$LIB_DIR"
for module in de_vault.py de_users.py de_keys.py de_patterns.py de_certs.py de_filter.py; do
    install -m 0640 -o root -g root "$HERE/pylib/$module" "$LIB_DIR/$module"
done
chmod 0750 "$LIB_DIR"

say "ставлю скрипты в $BIN_DIR"
for script in de-deps de-selftest de-ca de-encrypt de-rotate de-login-auth de-offline-auth de-vault de-session de-cleanup de-netns-user de-proxy-relay de-enter-net de-box de-screen de-agent de-palette de-report de-policy de-keywatch de-traffic de-init de-update; do
    install -m 0750 -o root -g root "$HERE/bin/$script" "$BIN_DIR/$script"
done
install -m 0755 -o root -g root "$HERE/bin/de-session-start" /usr/local/bin/de-session-start
install -m 0755 -o root -g root "$HERE/bin/de-note-command" /usr/local/bin/de-note-command

say "ставлю запись команд консоли"
mkdir -p /etc/profile.d
install -m 0644 -o root -g root "$HERE/config/de-history.sh" /etc/profile.d/de-history.sh

if [ ! -f "$CONFIG_FILE" ]; then
    say "создаю $CONFIG_FILE"
    install -m 0640 -o root -g root "$HERE/config/computers.conf.example" "$CONFIG_FILE"
else
    say "конфиг уже есть, не перезаписываю"
fi

say "ставлю список «не вскрывать»"
install -m 0644 -o root -g root "$HERE/proxy/policy/no-open.txt" /etc/digital-examen/no-open.txt

. "$CONFIG_FILE"
GROUP="${DE_USER_GROUP:-de-students}"

if [ "$MODE" = "update" ]; then
    PROTECTED="${DE_PROTECTED_USERS:-root}"
    say "обновление: список защищённых пользователей не трогаю"
else
say "защищаю всех существующих пользователей"
EXISTING=`getent passwd | cut -d: -f1 | sort -u | tr '\n' ' '`
PROTECTED=`echo "$EXISTING" | sed 's/  */ /g; s/^ //; s/ $//'`
TMP_CONFIG=`mktemp`
grep -v '^DE_PROTECTED_USERS=' "$CONFIG_FILE" > "$TMP_CONFIG"
echo "DE_PROTECTED_USERS=\"$PROTECTED\"" >> "$TMP_CONFIG"
cat "$TMP_CONFIG" > "$CONFIG_FILE"
rm -f "$TMP_CONFIG"
chmod 0640 "$CONFIG_FILE"
fi

say "создаю каталог базы песочницы"
mkdir -p "${DE_BOX_BASE:-/var/lib/digital-examen/box-base}"

say "создаю группу $GROUP"
getent group "$GROUP" >/dev/null 2>&1 || /usr/sbin/groupadd --system "$GROUP"

KEY_GROUP="${DE_KEY_SUDO_GROUP:-de-keyholders}"
say "создаю группу $KEY_GROUP для владельцев ключа"
getent group "$KEY_GROUP" >/dev/null 2>&1 || /usr/sbin/groupadd --system "$KEY_GROUP"

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
            print "auth        [success=done default=ignore]        pam_exec.so expose_authtok quiet /usr/local/sbin/de-offline-auth"
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

say "готовлю файлы секретов этого компьютера"
for SECRET_FILE in \
    "${DE_PROXY_SECRET_FILE:-/etc/digital-examen/proxy.secret}" \
    "${DE_COMMAND_SECRET_FILE:-/etc/digital-examen/command.secret}" \
    "${DE_SCREEN_SECRET_FILE:-/etc/digital-examen/screen.secret}" \
    "${DE_VAULT_SECRET_FILE:-/etc/digital-examen/vault.secret}"; do
    [ -f "$SECRET_FILE" ] || touch "$SECRET_FILE"
    chmod 0600 "$SECRET_FILE"
    chown root:root "$SECRET_FILE"
done
say "секреты выдаст сайт при постановке на учёт, свои на каждый компьютер"

say "создаю каталоги хранилища офлайн-входа и очереди кадров"
mkdir -p "${DE_VAULT_DIR:-/var/lib/digital-examen/vault}"
chmod 0700 "${DE_VAULT_DIR:-/var/lib/digital-examen/vault}"
mkdir -p "${DE_SCREEN_QUEUE_DIR:-/var/lib/digital-examen/screen-queue}"
chmod 0700 "${DE_SCREEN_QUEUE_DIR:-/var/lib/digital-examen/screen-queue}"

say "ставлю сеанс со школьным прокси"
mkdir -p /usr/share/xsessions
install -m 0644 -o root -g root "$HERE/xsessions/00-de-student.desktop" /usr/share/xsessions/00-de-student.desktop

say "проверяю и ставлю правила sudo"
for rule in de-students de-keyholders; do
    TMP_SUDO=`mktemp`
    cat "$HERE/config/sudoers.$rule" > "$TMP_SUDO"
    chmod 0440 "$TMP_SUDO"
    if /usr/sbin/visudo -cqf "$TMP_SUDO"; then
        install -m 0440 -o root -g root "$TMP_SUDO" "/etc/sudoers.d/$rule"
        rm -f "$TMP_SUDO"
    else
        rm -f "$TMP_SUDO"
        fail "правило sudo $rule не прошло проверку, ничего не менял"
    fi
done

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
for unit in de-secure.service de-cleanup.service de-screen.service de-agent.service de-keywatch.service de-traffic.service de-update.service de-update.timer de-vault-sync.service de-vault-sync.timer de-rotate.service de-rotate.timer; do
    install -m 0644 -o root -g root "$HERE/systemd/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable de-secure.service
systemctl enable de-cleanup.service
systemctl enable de-screen.service
systemctl enable de-agent.service
systemctl enable de-keywatch.service
systemctl enable de-traffic.service
systemctl enable de-update.timer
systemctl enable de-vault-sync.timer
systemctl enable de-rotate.timer

say "проверяю, что есть всё для сетевого замка и песочницы"
for tool in ip nft setpriv rsync systemd-run; do
    command -v "$tool" >/dev/null 2>&1 || [ -x "/usr/sbin/$tool" ] || fail "не найден $tool"
done
command -v systemd-nspawn >/dev/null 2>&1 || say "systemd-nspawn не найден, песочница de-box работать не будет"

say "проверяю конфиг sshd"
/usr/sbin/sshd -t || fail "sshd не принял конфиг"
systemctl reload sshd >/dev/null 2>&1 || true

if [ "$MODE" = "update" ]; then
    say "обновление применено, перезапускаю службы"
    systemctl start de-secure.service >/dev/null 2>&1 || true
    for unit in de-screen de-agent de-keywatch de-traffic; do
        systemctl restart "$unit.service" >/dev/null 2>&1 || true
    done
    systemctl start de-vault-sync.timer >/dev/null 2>&1 || true
    /usr/local/sbin/de-vault sync >/dev/null 2>&1 || true
    say "готово"
    exit 0
fi

say "включаю шифрование секретов и записей"
if [ "${DE_SECURE_DISK:-1}" = "1" ]; then
    "$BIN_DIR/de-encrypt" enable || say "шифрование не включилось, смотрите журнал"
    "$BIN_DIR/de-encrypt" status
else
    say "шифрование выключено в конфиге (DE_SECURE_DISK=0)"
fi

say "ставлю компьютер на учёт"
/usr/bin/python3 "$BIN_DIR/de-init" || fail "постановка на учёт не завершена, запустите de-init и повторите"

say "перезапускаю службы"
systemctl daemon-reload
for unit in de-cleanup de-screen de-agent de-keywatch de-traffic; do
    systemctl restart "$unit.service" >/dev/null 2>&1 || true
done
systemctl start de-update.timer >/dev/null 2>&1 || true
systemctl start de-vault-sync.timer >/dev/null 2>&1 || true
systemctl start de-rotate.timer >/dev/null 2>&1 || true

say "защищённые пользователи: $PROTECTED"

if [ "${DE_FILTER:-0}" = "1" ]; then
    say "создаю свой удостоверяющий центр для фильтра"
    /usr/bin/python3 "$BIN_DIR/de-ca" make || say "центр не создан, фильтр работать не будет"
fi

say "проверяю сам себя"
/usr/bin/python3 "$BIN_DIR/de-selftest" || say "проверка нашла плохое, прочитайте строки выше"

say "готово, перезагружаюсь"
say "после перезагрузки на экране входа вводится логин и пароль с сайта"
sleep 5
systemctl reboot
