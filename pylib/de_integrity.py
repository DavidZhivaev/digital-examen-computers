"""Report privileged commands that target Digital Examen on this host."""
import json
from pathlib import Path
import re
import shlex

import de_common

STATE = Path(de_common.STATE_DIR) / "integrity-seen.json"
TAG = "de-integrity:v1:"
ACTIONS = {"stop", "disable", "mask", "kill", "restart", "try-restart", "freeze"}


def intervention(message, installed):
    actor, sep, command = message.partition("COMMAND=")
    login = actor.strip().split(" ", 1)[0]
    if not sep or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", login):
        return None, None, []
    try:
        words = shlex.split(command)
    except ValueError:
        return None, None, []
    if not words:
        return None, None, []
    program = Path(words[0]).name
    args = [word for word in words[1:] if not word.startswith("-")]
    units = set()
    action = program
    if program == "systemctl" and args and args[0] in ACTIONS:
        if any(word in {"--user", "--global", "-H", "--host", "-M", "--machine"}
               or word.startswith(("--host=", "--machine=")) for word in words[1:]):
            return None, None, []
        action = "systemctl " + args[0]
        units = {(arg if "." in arg else arg + ".service") for arg in args[1:]} & installed
    elif program in {"killall", "pkill"}:
        units = {arg + ".service" for arg in args} & installed
    elif program == "nft" and args in (["flush", "ruleset"], ["delete", "table", "inet", "de_agent"],
                                       ["flush", "table", "inet", "de_agent"]):
        units = {"de-net.service"} & installed
        action = "nft " + " ".join(args)
    elif program in {"rm", "mv", "chmod", "chown", "truncate", "unlink"}:
        for arg in args:
            path = Path(arg)
            if path.parent == Path("/etc/systemd/system") and path.name in installed:
                units.add(path.name)
            elif path.is_absolute() and any(
                path == root or root in path.parents
                for root in map(Path, ("/opt/digital-examen-computers", "/etc/digital-examen",
                                       "/usr/local/lib/digital-examen"))
            ):
                units.update({"de-agent.service"} & installed)
    return login, action, sorted(units)


def main():
    api = de_common.setting("DE_API_URL").rstrip("/")
    name = de_common.computer()
    if not api.startswith("https://") or not name:
        return
    installed = {path.name for path in Path("/opt/digital-examen-computers/systemd").glob("de-*")}
    since = "-1day"
    try:
        seen = json.loads(STATE.read_text())
    except (OSError, ValueError):
        since = "-5min"
        try:
            seen = json.loads((Path(de_common.RUN_DIR) / STATE.name).read_text())
        except (OSError, ValueError):
            seen = []
        de_common.write_file(str(STATE), json.dumps(seen), mode=0o600)
    code, rows = de_common.run([
        "journalctl", "-t", "sudo", "--since=" + since, "-o", "json", "--no-pager",
    ], 10)
    if code != 0:
        return
    boot = next((int(line.split()[1]) for line in de_common.read_file("/proc/stat").splitlines()
                 if line.startswith("btime ")), 0)
    for row in rows.splitlines():
        try:
            event = json.loads(row)
            identifier = event["__CURSOR"]
            stamp = int(event["__REALTIME_TIMESTAMP"]) // 1_000_000
            login, action, units = intervention(event["MESSAGE"], installed)
        except (ValueError, KeyError, TypeError):
            continue
        if not login or not units or identifier in seen:
            continue
        unrecovered = [unit for unit in units[:5]
                       if (de_common.run(["systemctl", "is-active", "--quiet", unit], 5)[0] != 0
                           or de_common.run(["systemctl", "is-enabled", "--quiet", unit], 5)[0] != 0)]
        payload = json.dumps({"boot": boot if stamp >= boot else 0, "action": action,
                              "units": units[:5], "unrecovered": unrecovered},
                             separators=(",", ":"))
        try:
            answer = de_common.send_json(api + "/api/computers/device/traffic", {
                "computer": name, "login": login,
                "entries": [{"kind": "alarm", "at": stamp, "value": TAG + payload}],
            }, seconds=8)
        except (OSError, ValueError):
            continue
        if answer.get("ok") is True:
            seen.append(identifier)
            de_common.write_file(str(STATE), json.dumps(seen[-4096:]), mode=0o600)


if __name__ == "__main__":
    main()
