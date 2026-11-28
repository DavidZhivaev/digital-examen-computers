"""Track changes to the NetworkManager connection used for internet access."""
import json
import ipaddress
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

import de_common
import de_freeze

STATE = Path(de_common.STATE_DIR) / "network-events.json"
BOOT = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
LOCK = threading.Lock()
WAKE = threading.Event()
RETENTION = 14 * 86400


def load():
    try:
        value = json.loads(STATE.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


STATE_DATA = load()


def save():
    de_common.write_file(str(STATE), json.dumps(STATE_DATA), mode=0o600)


def nmcli(*args):
    return subprocess.check_output(
        ["nmcli", "--escape", "no", *args], text=True, timeout=3,
        stderr=subprocess.DEVNULL, env=dict(os.environ, LC_ALL="C.UTF-8"),
    ).rstrip("\n")


def selected_connection(refresh_address=False):
    fallback = None
    active = nmcli("-t", "-f", "UUID,TYPE,DEVICE,ACTIVE-PATH", "connection", "show", "--active")
    for line in active.splitlines():
        key, kind, device, activation = line.split(":", 3)
        if kind not in {"802-11-wireless", "802-3-ethernet", "gsm", "cdma", "pppoe", "wireguard"} or not device:
            continue
        candidate = key, kind, device, activation
        defaults = nmcli("-g", "GENERAL.DEFAULT,GENERAL.DEFAULT6", "connection", "show", key)
        if "yes" in defaults.splitlines():
            fallback = candidate
            break
        if fallback is None or len(STATE_DATA.get("key") or []) > 5 and STATE_DATA["key"][5] == activation:
            fallback = candidate
    if fallback is None:
        return None
    key, kind, device, activation = fallback
    # Request only identity, never password or other connection secrets.
    name = nmcli("-g", "connection.id", "connection", "show", key)
    if kind == "802-11-wireless":
        name = nmcli("-g", "802-11-wireless.ssid", "connection", "show", key) or name
    login = nmcli("-g", "802-1x.identity", "connection", "show", key)
    if not login and kind in {"gsm", "cdma", "pppoe"}:
        login = nmcli("-g", kind + ".username", "connection", "show", key)
    if kind == "802-3-ethernet":
        name = "Проводное соединение"
    identity = [key, device, name, login if login != "--" else "", activation]
    previous = STATE_DATA.get("key") or []
    if not refresh_address and previous[:6] == [BOOT, *identity] and len(previous) == 7:
        return [*identity, previous[6]]
    addresses = nmcli("-g", "IP4.ADDRESS,IP6.ADDRESS", "device", "show", device)
    local_ip = next((str(ipaddress.ip_interface(value).ip) for value in addresses.splitlines()
                     if value and value != "--" and not ipaddress.ip_interface(value).ip.is_link_local), "")
    return [key, device, name, login if login != "--" else "", activation, local_ip]


def active_interface():
    with LOCK:
        key = STATE_DATA.get("key")
        return key[2] if key and key[0] == BOOT else None


def interface_bytes(device):
    stats = Path("/sys/class/net") / device / "statistics"
    return sum(int((stats / field).read_text()) for field in ("rx_bytes", "tx_bytes"))


def monitor_changes():
    while True:
        try:
            with subprocess.Popen(["stdbuf", "-oL", "nmcli", "monitor"], stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True) as process:
                for _ in process.stdout:
                    WAKE.set()
        except OSError:
            pass
        time.sleep(3)


def monitor():
    threading.Thread(target=monitor_changes, daemon=True).start()
    last = None
    while True:
        if de_freeze.frozen():
            last = None
            WAKE.wait(1)
            WAKE.clear()
            continue
        try:
            connection = selected_connection(WAKE.is_set())
            stamp = int(time.time())
            sample = (time.monotonic(), interface_bytes(connection[1])) if connection else None
            with LOCK:
                key = [BOOT, *connection] if connection else None
                pending = STATE_DATA.setdefault("pending", {})
                if key != STATE_DATA.get("key"):
                    for event_id, event in list(pending.items()):
                        if event["at"] < stamp - RETENTION:
                            del pending[event_id]
                    STATE_DATA["key"] = key
                    STATE_DATA["current"] = None
                    last = None
                    if connection:
                        STATE_DATA["current"] = {
                            "id": uuid.uuid4().hex, "at": stamp, "name": connection[2][:255],
                            "login": connection[3][:255], "local_ip": connection[5], "bytes": 0, "seconds": 0,
                        }
                current = STATE_DATA.get("current")
                if current:
                    if last and sample[1] >= last[1]:
                        current["bytes"] += sample[1] - last[1]
                        current["seconds"] += sample[0] - last[0]
                    pending[current["id"]] = current.copy()
                save()
                last = sample
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        WAKE.wait(5)
        WAKE.clear()


def snapshot(now):
    with LOCK:
        if "key" not in STATE_DATA:
            return None
        saved = STATE_DATA.get("current")
        current = saved["id"] if saved and (STATE_DATA.get("key") or [None])[0] == BOOT else None
        pending = STATE_DATA.get("pending", {})
        events = ([pending[current].copy()] if current in pending else []) + [
            event.copy() for key, event in pending.items() if key != current and event["at"] >= now - RETENTION
        ]
        return {"current": current, "events": events[:40]}


def acknowledge(report):
    with LOCK:
        pending = STATE_DATA.get("pending", {})
        for event in report["events"]:
            if pending.get(event["id"]) == event:
                del pending[event["id"]]
        save()
