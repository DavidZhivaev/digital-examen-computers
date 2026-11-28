import glob
import grp
import json
import os
import pwd
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request

CONFIG_FILE = "/etc/digital-examen/computers.conf"
RUN_DIR = "/run/digital-examen"
STATE_DIR = "/var/lib/digital-examen"
SCREEN_ENABLED_FILE = os.path.join(STATE_DIR, "screen-enabled")
LOG_FILE = "/var/log/digital-examen-computers.log"


def read_config():
    values = {}
    if not os.path.exists(CONFIG_FILE):
        return values
    for line in open(CONFIG_FILE):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name.strip()] = value
    return values


CONFIG = read_config()


def setting(name, default=""):
    return CONFIG.get(name, default)


def log(who, message):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        out = open(LOG_FILE, "a")
        out.write(stamp + " " + who + " " + message + "\n")
        out.close()
    except IOError:
        pass


def read_file(path):
    try:
        return open(path).read().strip()
    except (IOError, OSError):
        return ""


def write_file(path, text, mode=0o644):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=folder or ".", prefix=".de-")
    try:
        with os.fdopen(descriptor, "w") as out:
            out.write(text)
            os.fchmod(out.fileno(), mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def computer():
    return setting("DE_COMPUTER_ID")


def hostname_place(hostname):
    match = re.fullmatch(r"([a-z])1580-([0-9]{1,2})-([a-z0-9]{1,16})-([0-9]{1,3})", hostname)
    if match:
        prefix, corpus, room, number = match.groups()
        if room.isdecimal():
            room = room.lstrip("0")
        if int(corpus) and room and int(number):
            return int(corpus), room, int(number), "" if prefix == "m" else prefix
    return None


def place_name(place):
    corpus, room, number, prefix = place
    return f"{prefix}{corpus}k-{room}-{number}"


def write_place(name, place, **settings):
    corpus, room, number, _prefix = place
    values = read_config()
    values.update(settings, DE_COMPUTER_ID=name, DE_COMPUTER_CORPUS=corpus,
                  DE_COMPUTER_ROOM=room, DE_COMPUTER_NUMBER=number)
    lines = ['%s="%s"\n' % (key, value) for key, value in values.items()]
    write_file(CONFIG_FILE, "".join(lines), 0o640)


def secret(kind):
    return read_file("/etc/digital-examen/%s.secret" % kind)


def run(command, seconds=10, env=None):
    try:
        done = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=seconds, env=env)
    except subprocess.TimeoutExpired:
        return 124, "превышено время"
    except OSError as error:
        return 127, str(error)
    return done.returncode, done.stdout.decode("utf-8", "replace")


def send_json(url, payload, kind="proxy", seconds=20, identity=None):
    request = urllib.request.Request(url, data=json.dumps(payload).encode())
    request.add_header("Content-Type", "application/json")
    request.add_header("X-De-Computer", identity or computer())
    request.add_header("X-De-Secret", secret(kind))
    answer = urllib.request.urlopen(request, timeout=seconds)
    return json.loads(answer.read().decode("utf-8"))


def loginctl():
    # Some managed images contain a non-executable /usr/bin/loginctl stub.
    found = shutil.which("loginctl")
    if found:
        return found
    for path in ("/usr/bin/loginctl", "/bin/loginctl"):
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return "loginctl"


def session_fields(session):
    code, output = run([loginctl(), "show-session", session, "-p", "Name", "-p", "Active",
                        "-p", "Type", "-p", "Display", "-p", "Class", "-p", "Seat",
                        "-p", "Leader", "-p", "User", "-p", "State", "-p", "LockedHint"], 5)
    values = {}
    if code != 0:
        return values
    for line in output.splitlines():
        if "=" in line:
            name, value = line.split("=", 1)
            values[name.strip()] = value.strip()
    values["Id"] = session
    return values


def sessions():
    code, output = run([loginctl(), "list-sessions", "--no-legend"], 5)
    if code != 0:
        return []
    out = []
    for line in output.splitlines():
        parts = line.split()
        if parts:
            out.append(session_fields(parts[0]))
    return out


def screen_session():
    code, seat = run([loginctl(), "show-seat", "seat0", "-p", "ActiveSession", "--value"], 3)
    if code == 0 and seat.strip():
        active = graphical_session(session_fields(seat.strip()))
        if active is not None:
            return active
    for values in sessions():
        if graphical_session(values) is not None:
            return values
    return None


def graphical_session(values):
    if values.get("Active") == "yes" and values.get("Type") in ("x11", "wayland"):
        return values
    return None


def current_user():
    values = screen_session()
    if values is None or values.get("Class") == "greeter":
        return ""
    return values.get("Name", "")


def process_env(pid):
    try:
        data = open("/proc/%s/environ" % pid, "rb").read()
    except (IOError, OSError):
        return {}
    values = {}
    for item in data.split(b"\0"):
        if b"=" not in item:
            continue
        name, value = item.split(b"=", 1)
        values[name.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return values


def user_env(uid, need="DBUS_SESSION_BUS_ADDRESS"):
    best = {}
    for path in glob.glob("/proc/[0-9]*"):
        try:
            if os.stat(path).st_uid != uid:
                continue
        except OSError:
            continue
        values = process_env(path[6:])
        if not values.get(need):
            continue
        if values.get("WAYLAND_DISPLAY") or values.get("DISPLAY"):
            if len(values) > len(best):
                best = values
    return best


def session_env(entry, values):
    names = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS")
    env = {name: values[name] for name in names if values.get(name)}
    env.update(HOME=entry.pw_dir, XDG_RUNTIME_DIR="/run/user/%d" % entry.pw_uid,
               PATH="/usr/bin:/bin")
    return env


def groups_of(login):
    names = []
    try:
        entry = pwd.getpwnam(login)
        names.append(grp.getgrgid(entry.pw_gid).gr_name)
    except KeyError:
        pass
    for group in grp.getgrall():
        if login in group.gr_mem:
            names.append(group.gr_name)
    return names


def is_admin(login):
    try:
        entry = pwd.getpwnam(login)
    except KeyError:
        return False
    if entry.pw_uid == 0 or set(groups_of(login)) & {"wheel", "sudo", "admin", "root"}:
        return True
    return False


def people():
    with open("/etc/shells") as source:
        shells = {line.strip() for line in source if line.startswith("/")}
    out = []
    for entry in pwd.getpwall():
        if entry.pw_shell not in shells or entry.pw_shell.endswith(("nologin", "false")):
            continue
        if is_admin(entry.pw_name):
            continue
        privileges = subprocess.run(
            ["sudo", "-n", "-l", "-U", entry.pw_name],
            env=dict(os.environ, LC_ALL="C"), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        if not any(line.startswith("User " + entry.pw_name + " may run the following commands")
                   for line in privileges.stdout.splitlines()):
            out.append(entry)
    return out
