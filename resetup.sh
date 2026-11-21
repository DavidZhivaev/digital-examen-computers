#!/bin/sh
set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo=/opt/digital-examen-computers
state=/var/lib/digital-examen
config=/etc/digital-examen
image=$state/secure.img
mountpoint=$state/secure
mapper=/dev/mapper/de-secure
remove=${1:-}

[ "$(id -u)" = 0 ] || { echo "Запустите sudo sh resetup.sh" >&2; exit 1; }
case "$remove" in ""|--remove) ;; *) exit 2 ;; esac

if ! findmnt -rn -o TARGET | awk -v mount="$mountpoint" -v state="$state" '
    $0 != mount && ($0 == state || index($0, state "/") == 1 ||
        index($0, "/etc/digital-examen/") == 1 ||
        index($0, "/opt/digital-examen-computers/") == 1) { print; failed=1 }
    END { exit failed }
'; then
    echo "Обнаружено дополнительное монтирование. Откат остановлен." >&2
    exit 1
fi

if [ -e "$image" ] || [ -L "$image" ] || [ -e "$mapper" ] || mountpoint -q "$mountpoint"; then
    [ -f "$image" ] && [ ! -L "$image" ] || {
        echo "Не подтверждено происхождение хранилища $mountpoint" >&2
        exit 1
    }
    loop=$(losetup -j "$image" | cut -d: -f1)
    source=$(findmnt -n -o SOURCE --mountpoint "$mountpoint" || true)
    if [ -n "$source" ] && [ "$source" != "$mapper" ]; then
        echo "В $mountpoint смонтировано неизвестное устройство: $source" >&2
        exit 1
    fi
    if [ -e "$mapper" ]; then
        backend=$(LC_ALL=C cryptsetup status de-secure | awk '$1 == "device:" {print $2}')
        [ -n "$loop" ] && [ "$backend" = "$loop" ] || {
            echo "de-secure не принадлежит файлу secure.img" >&2
            exit 1
        }
    fi
    if [ -z "$remove" ] && [ -z "$source" ] &&
       [ -L "$config/screen.secret" ]; then
        systemctl start de-secure.service
        [ "$(findmnt -n -o SOURCE --mountpoint "$mountpoint")" = "$mapper" ]
        loop=$(losetup -j "$image" | cut -d: -f1)
        [ -n "$loop" ]
        [ "$(LC_ALL=C cryptsetup status de-secure | awk '$1 == "device:" {print $2}')" = "$loop" ]
    fi
fi

if [ -z "$remove" ] && [ "$here" != "$repo" ] && [ -d "$repo/.git" ]; then
    git -C "$repo" fetch --no-tags "$here" HEAD
    git -C "$repo" merge --ff-only FETCH_HEAD
    cmp -s "$here/setup.sh" "$repo/setup.sh" || {
        echo "Версии setup.sh различаются. Обновите $repo и повторите." >&2
        exit 1
    }
fi

accounts=
if [ -e "$state/users" ] || [ -e "$image" ]; then
    if group=$(getent group de-students); then
        gid=$(printf '%s\n' "$group" | cut -d: -f3)
        accounts=$(awk -F: -v group="$gid" '
            $4 == group && $3 >= 20000 && $3 <= 29999 {
                if ($5 == "digital-examen alias") print $1
                else if ($5 == "digital-examen") canonical[++count]=$1
            }
            END { for (i=1; i<=count; i++) print canonical[i] }
        ' /etc/passwd)
        for account in $accounts; do
            if pgrep -u "$account" >/dev/null 2>&1; then
                echo "Завершите активный сеанс старого агента: $account" >&2
                exit 1
            fi
        done
    fi
fi

for unit in de-vault-sync.timer de-rotate.timer de-cleanup.service de-keywatch.service \
    de-vault-sync.service de-rotate.service de-netns.service de-agent.service de-screen.service \
    de-traffic.service de-inspect.service de-net.service de-update.timer de-update.service de-secure.service; do
    if [ -f "/etc/systemd/system/$unit" ]; then
        systemctl disable --now "$unit"
    fi
done

for file in /etc/pam.d/sddm /etc/pam.d/login; do
    [ -f "$file" ] || continue
    grep -qx '# digital-examen-computers begin' "$file" || continue
    temp=$(mktemp "${file}.de.XXXXXX")
    if ! awk '
        /^# digital-examen-computers begin$/ { if (inside) exit 1; inside=1; next }
        /^# digital-examen-computers end$/ { if (!inside) exit 1; inside=0; next }
        !inside { print }
        END { if (inside) exit 1 }
    ' "$file" > "$temp"; then
        rm -f "$temp"
        echo "Неполный блок агента в $file" >&2
        exit 1
    fi
    chown --reference="$file" "$temp"
    chmod --reference="$file" "$temp"
    mv -f "$temp" "$file"
done

rm -f /etc/sddm.conf.d/90-digital-examen.conf /usr/share/xsessions/00-de-student.desktop \
      /etc/sudoers.d/de-students /etc/sudoers.d/de-keyholders
if [ -f /etc/ssh/sshd_config.d/90-digital-examen.conf ]; then
    rm -f /etc/ssh/sshd_config.d/90-digital-examen.conf
    if systemctl is-active --quiet sshd.service; then
        /usr/sbin/sshd -t
        systemctl reload sshd.service
    fi
fi

if [ -z "$remove" ]; then
    for kind in command proxy screen; do
        file=$config/$kind.secret
        [ -L "$file" ] || continue
        [ "$(readlink "$file")" = "$mountpoint/secrets/$kind.secret" ]
        [ -s "$file" ]
        temp=$(mktemp "$file.XXXXXX")
        install -m 0600 "$file" "$temp"
        mv -f "$temp" "$file"
    done
    if [ -d "$state/screen-queue.new" ] && [ ! -e "$state/screen-queue" ]; then
        mv -T "$state/screen-queue.new" "$state/screen-queue"
    fi
    if [ -L "$state/screen-queue" ]; then
        [ "$(readlink "$state/screen-queue")" = "$mountpoint/screen-queue" ]
        temp=$(mktemp -d "$state/.screen-queue.XXXXXX")
        cp -a "$state/screen-queue/." "$temp/"
        mv -fT "$temp" "$state/screen-queue.new"
        rm "$state/screen-queue"
        mv -T "$state/screen-queue.new" "$state/screen-queue"
    fi
fi

if [ -f "$state/ca/ca.crt" ] && [ ! -L "$state/ca" ]; then
    python3 - "$state/ca" <<'PY'
import json
import shutil
import subprocess
import sys
from pathlib import Path

directory = Path(sys.argv[1])
certificate = directory / "ca.crt"
content = certificate.read_bytes()
removed = False
for root in ("/etc/pki/trust/anchors", "/usr/local/share/ca-certificates", "/etc/pki/ca-trust/source/anchors"):
    path = Path(root, "digital-examen.crt")
    if not path.is_symlink() and path.is_file() and path.read_bytes() == content:
        path.unlink()
        removed = True

if removed:
    for command in (["update-ca-certificates"], ["update-ca-trust", "extract"]):
        if shutil.which(command[0]) and subprocess.call(command) == 0:
            break
    else:
        raise SystemExit("Не удалось обновить системное хранилище сертификатов")

expected = {"policies": {"Certificates": {"ImportEnterpriseRoots": True, "Install": [str(certificate)]}}}
for root in ("/usr/lib64/firefox/distribution", "/usr/lib/firefox/distribution", "/etc/firefox/policies"):
    path = Path(root, "policies.json")
    if path.is_symlink():
        continue
    try:
        matched = json.loads(path.read_text()) == expected
    except (OSError, ValueError):
        continue
    if matched:
        path.unlink()

for path in (directory / "ca.key", certificate):
    if path.exists():
        path.unlink()
PY
fi
rmdir "$state/ca" "$state/box-base" 2>/dev/null || true

for account in $accounts; do
    /usr/sbin/userdel "$account"
done

if mountpoint -q "$mountpoint"; then umount "$mountpoint"; fi
if [ -e "$mapper" ]; then cryptsetup close de-secure; fi
if [ -n "${loop:-}" ]; then losetup -d "$loop"; fi
rm -f "$image" "$state/secure.json" "$config/secure.key" "$config/vault.secret"
rm -rf --one-file-system "$state/vault"
if [ -L "$state/users" ] && [ "$(readlink "$state/users")" = "$mountpoint/users" ]; then
    rm "$state/users"
fi

for unit in de-vault-sync.timer de-vault-sync.service de-rotate.timer de-rotate.service \
    de-cleanup.service de-keywatch.service de-netns.service de-secure.service; do
    rm -f "/etc/systemd/system/$unit"
done
for script in de-box de-ca de-cleanup de-deps de-encrypt de-enter-net de-keywatch de-login-auth \
    de-netns-user de-offline-auth de-palette de-policy de-proxy-relay de-report de-rotate \
    de-selftest de-session de-vault; do
    rm -f "/usr/local/sbin/$script"
done
for module in de_certs.py de_filter.py de_time.py de_users.py de_vault.py; do
    rm -f "/usr/local/lib/digital-examen/$module"
done
rm -f /usr/local/bin/de-session-start
systemctl daemon-reload

if [ "$remove" = --remove ]; then
    if [ -f /usr/local/lib/digital-examen/de_firefox_policy.py ]; then
        PYTHONPATH=/usr/local/lib/digital-examen /usr/bin/python3 -m de_firefox_policy remove
    fi
    if [ -f "$state/installed-files" ]; then
        while IFS= read -r path; do
            case "$path" in
                /etc/systemd/system/de-*.service|/etc/systemd/system/de-*.timer)
                    [ -f "$path" ] || continue
                    systemctl disable --now "${path##*/}"
                    unlink "$path"
                    ;;
                /usr/local/sbin/de-*|/usr/local/bin/de|/usr/local/bin/de-note-command|/usr/local/bin/de-tabs-native|/etc/*/native-messaging-hosts/ru.digital_examen.tabs.json|/etc/*/policies/managed/digital-examen-tabs.json|/usr/lib*/mozilla/native-messaging-hosts/ru.digital_examen.tabs.json|/usr/local/lib/digital-examen/de_*.py|/etc/profile.d/de-history.sh)
                    [ ! -f "$path" ] || unlink "$path"
                    ;;
            esac
        done < "$state/installed-files"
    fi
    for unit in de-agent.service de-screen.service de-traffic.service de-net.service de-update.service de-update.timer; do
        rm -f "/etc/systemd/system/$unit"
    done
    for script in de-agent de-screen de-traffic de-net de-init de-update; do
        rm -f "/usr/local/sbin/$script"
    done
    rm -f /etc/profile.d/de-history.sh /usr/local/bin/de /usr/local/bin/de-note-command \
        "$config/computers.conf" "$config/command.secret" "$config/proxy.secret" \
        "$config/screen.secret" "$config/key-public.pem" "$config/no-open.txt"
    if [ -f /etc/pki/ca-trust/source/anchors/digital-examen-inspection.crt ]; then
        rm -f /etc/pki/ca-trust/source/anchors/digital-examen-inspection.crt
        update-ca-trust extract
    fi
    rm -rf --one-file-system /var/lib/de-inspect "$state/packages"
    rm -f "$config/inspect-hosts" "$config/mass-scp.conf"
    rm -rf --one-file-system "$state/screen-queue" /usr/local/lib/digital-examen /run/digital-examen
    rm -f "$state/policy.json" "$state/traffic-queue.jsonl" "$state/traffic-queue.jsonl.sending" "$state/installed-revision" "$state/installed-script" "$state/installed-files" /var/log/digital-examen-computers.log
    if [ -d "$repo/.git" ]; then rm -rf --one-file-system "$repo"; fi
    systemctl daemon-reload
    echo "Агент удалён"
    exit 0
fi

if [ -d "$repo/.git" ]; then
    exec sh "$repo/setup.sh"
fi
exec sh "$here/setup.sh"
