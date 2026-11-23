"""Report sudo commands that target installed Digital Examen services."""
import json
from pathlib import Path
import re
import shlex

import de_common

STATE = Path(de_common.RUN_DIR) / "integrity-seen.json"
TAG = "de-integrity:v1:"
ACTIONS = {"stop", "disable", "mask", "kill"}


def target_units(message, installed):
    actor, sep, command = message.partition("COMMAND=")
    if not sep:
        return None, []
    try:
        words = shlex.split(command)
    except ValueError:
        return None, []
    if not words or Path(words[0]).name != "systemctl":
        return None, []
    if any(word in {"--user", "--global", "-H", "--host", "-M", "--machine"}
           or word.startswith(("--host=", "--machine=")) for word in words[1:]):
        return None, []
    args = [word for word in words[1:] if not word.startswith("-")]
    if not args or args[0] not in ACTIONS:
        return None, []
    units = set()
    for arg in args[1:]:
        unit = arg if "." in arg else arg + ".service"
        if unit in installed:
            units.add(unit)
    login = actor.strip().split(" ", 1)[0]
    return login if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", login) else None, sorted(units)


def main():
    api = de_common.setting("DE_API_URL").rstrip("/")
    name = de_common.computer()
    if not api.startswith("https://") or not name:
        return
    installed = {path.name for path in Path("/opt/digital-examen-computers/systemd").glob("de-*")}
    code, rows = de_common.run([
        "journalctl", "-b", "-t", "sudo", "--since=-5min", "-o", "json", "--no-pager",
    ], 10)
    if code != 0:
        return
    try:
        seen = json.loads(STATE.read_text())
    except (OSError, ValueError):
        seen = []
    boot = next((int(line.split()[1]) for line in de_common.read_file("/proc/stat").splitlines()
                 if line.startswith("btime ")), 0)
    for row in rows.splitlines():
        try:
            event = json.loads(row)
            identifier = event["__CURSOR"]
            stamp = int(event["__REALTIME_TIMESTAMP"]) // 1_000_000
            login, units = target_units(event["MESSAGE"], installed)
        except (ValueError, KeyError, TypeError):
            continue
        if not login or not units or identifier in seen:
            continue
        unrecovered = [unit for unit in units
                       if (de_common.run(["systemctl", "is-active", "--quiet", unit], 5)[0] != 0
                           or de_common.run(["systemctl", "is-enabled", "--quiet", unit], 5)[0] != 0)]
        payload = json.dumps({"boot": boot, "units": units, "unrecovered": unrecovered},
                             separators=(",", ":"))
        try:
            answer = de_common.send_json(api + "/api/computers/device/traffic", {
                "computer": name, "login": login,
                "entries": [{"kind": "alarm", "at": stamp, "value": TAG + payload}],
            }, seconds=8)
        except (OSError, ValueError):
            continue
        if answer.get("stored") == 1:
            seen.append(identifier)
            de_common.write_file(str(STATE), json.dumps(seen[-300:]), mode=0o600)


if __name__ == "__main__":
    main()
