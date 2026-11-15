#!/bin/sh
set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo=/opt/digital-examen-computers
config=/etc/digital-examen/computers.conf
state=/var/lib/digital-examen

[ "$(id -u)" = 0 ] || { echo "Запустите sudo sh setup.sh" >&2; exit 1; }

if [ -e /etc/sddm.conf.d/90-digital-examen.conf ] ||
   [ -e /etc/systemd/system/de-secure.service ] ||
   [ -e "$state/secure.img" ]; then
    echo "Обнаружена прежняя установка. Запустите sudo sh resetup.sh." >&2
    exit 1
fi

if [ "$here" != "$repo" ]; then
    [ ! -e "$repo" ] || { echo "Запустите $repo/setup.sh" >&2; exit 1; }
    git clone --no-local -- "$here" "$repo"
    git -C "$repo" remote set-url origin https://github.com/DavidZhivaev/digital-examen-computers.git
    exec sh "$repo/setup.sh" "$@"
fi

sh "$here/bin/de-deps"
install -d -m 0755 /etc/digital-examen /usr/local/lib/digital-examen
install -d -m 0700 "$state" "$state/screen-queue"
manifest=$(mktemp "$state/.installed-files.XXXXXX")
trap 'unlink "$manifest" 2>/dev/null || true' EXIT
put() {
    install -m "$1" "$2" "$3"
    printf '%s\n' "$3" >> "$manifest"
}

for module in "$here"/pylib/de_*.py; do
    put 0644 "$module" "/usr/local/lib/digital-examen/${module##*/}"
done
for script in "$here"/bin/de-*; do
    name=${script##*/}
    case "$name" in
        de-deps) ;;
        de-note-command|de-tabs-native) put 0755 "$script" "/usr/local/bin/$name" ;;
        *) put 0750 "$script" "/usr/local/sbin/$name" ;;
    esac
done
put 0644 "$here/config/de-history.sh" /etc/profile.d/de-history.sh
for native in /etc/chromium/native-messaging-hosts /etc/chromium-browser/native-messaging-hosts \
    /etc/opt/chrome/native-messaging-hosts /etc/opt/edge/native-messaging-hosts; do
    install -d -m 0755 "$native"
    put 0644 "$here/browser/tabs/native-chromium.json" "$native/ru.digital_examen.tabs.json"
done
for managed in /etc/chromium/policies/managed /etc/chromium-browser/policies/managed \
    /etc/opt/chrome/policies/managed /etc/opt/edge/policies/managed; do
    install -d -m 0755 "$managed"
    put 0644 "$here/browser/tabs/managed-chromium.json" "$managed/digital-examen-tabs.json"
done
for native in /usr/lib/mozilla/native-messaging-hosts /usr/lib64/mozilla/native-messaging-hosts; do
    install -d -m 0755 "$native"
    put 0644 "$here/browser/tabs/native-firefox.json" "$native/ru.digital_examen.tabs.json"
done


if [ ! -f "$config" ]; then
    install -m 0640 "$here/config/computers.conf.example" "$config"
fi

if ! grep -Eq '^DE_COMPUTER_ID="?[^"]+"?$' "$config"; then
    [ "${1:-}" != --update ] || { echo "Компьютер не зарегистрирован" >&2; exit 1; }
    /usr/local/sbin/de-init
fi

enabled=
for definition in "$here"/systemd/de-*; do
    unit=${definition##*/}
    put 0644 "$definition" "/etc/systemd/system/$unit"
    if grep -qxF '[Install]' "$definition"; then enabled="$enabled $unit"; fi
done

if [ -f "$state/installed-files" ]; then
    while IFS= read -r path; do
        grep -qxF -- "$path" "$manifest" && continue
        case "$path" in
            /etc/systemd/system/de-*.service|/etc/systemd/system/de-*.timer)
                [ -e "$path" ] || continue
                systemctl disable --now "${path##*/}"
                unlink "$path"
                ;;
            /usr/local/sbin/de-*|/usr/local/bin/de-note-command|/usr/local/bin/de-tabs-native|/etc/*/native-messaging-hosts/ru.digital_examen.tabs.json|/etc/*/policies/managed/digital-examen-tabs.json|/usr/lib*/mozilla/native-messaging-hosts/ru.digital_examen.tabs.json|/usr/local/lib/digital-examen/de_*.py|/etc/profile.d/de-history.sh)
                [ ! -f "$path" ] || unlink "$path"
                ;;
        esac
    done < "$state/installed-files"
fi

systemctl daemon-reload
systemctl enable $enabled
for unit in $enabled; do
    case "$unit" in
        *.service) systemctl restart "$unit" ;;
        *.timer) systemctl start "$unit" ;;
    esac
done
mv -f "$manifest" "$state/installed-files"
git -C "$repo" rev-parse HEAD > "$state/installed-revision.new"
mv -f "$state/installed-revision.new" "$state/installed-revision"
