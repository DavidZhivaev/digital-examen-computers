import glob
import grp
import json
import os
import pwd
import subprocess
import tempfile
import time
import urllib.request

CONFIG_FILE = "/etc/digital-examen/computers.conf"
RUN_DIR = "/run/digital-examen"
STATE_DIR = "/var/lib/digital-examen"
LOG_FILE = "/var/log/digital-examen-computers.log"
GREETER = "_greeter"


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


def send_json(url, payload, kind="proxy", seconds=20):
    request = urllib.request.Request(url, data=json.dumps(payload).encode())
    request.add_header("Content-Type", "application/json")
    request.add_header("X-De-Computer", computer())
    request.add_header("X-De-Secret", secret(kind))
    answer = urllib.request.urlopen(request, timeout=seconds)
    return json.loads(answer.read().decode("utf-8"))


def loginctl():
    for path in ("/usr/bin/loginctl", "/bin/loginctl"):
        if os.path.exists(path):
            return path
    return "loginctl"


def session_fields(session):
    code, output = run([loginctl(), "show-session", session, "-p", "Name", "-p", "Active",
                        "-p", "Type", "-p", "Display", "-p", "Class", "-p", "Seat",
                        "-p", "Leader", "-p", "User", "-p", "State"], 5)
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
        return graphical_session(session_fields(seat.strip()))
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
    code, output = run(["sudo", "-n", "-l", "-U", login], 10)
    return code == 0 and any(line.lstrip().startswith("(") for line in output.splitlines())


def uid_min():
    value = 1000
    try:
        for line in open("/etc/login.defs"):
            parts = line.split()
            if len(parts) == 2 and parts[0] == "UID_MIN":
                value = int(parts[1])
    except (IOError, ValueError):
        pass
    return value


def people():
    low = uid_min()
    out = []
    for entry in pwd.getpwall():
        if entry.pw_uid < low or entry.pw_uid >= 60000:
            continue
        if entry.pw_shell.endswith("nologin") or entry.pw_shell.endswith("false"):
            continue
        if is_admin(entry.pw_name):
            continue
        out.append(entry)
    return out
