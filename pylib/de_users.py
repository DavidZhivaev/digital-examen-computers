import os
import pwd
import re
import subprocess
import time

STATE_DIR = "/var/lib/digital-examen/users"
RUN_DIR = "/run/digital-examen"


def canonical_name(login):
    return login.lower()


def login_is_safe(login, config):
    low = int(config.get("DE_LOGIN_MIN_LENGTH", "3"))
    high = int(config.get("DE_LOGIN_MAX_LENGTH", "31"))
    if len(login) < low or len(login) > high:
        return False
    return re.match(r"^[a-z][a-z0-9_-]*$", canonical_name(login)) is not None


def user_is_protected(login, config):
    names = config.get("DE_PROTECTED_USERS", "root").split()
    lowered = [name.lower() for name in names]
    return login in names or canonical_name(login) in lowered


def user_exists(login):
    try:
        pwd.getpwnam(login)
        return True
    except KeyError:
        return False


def state_exists(login):
    return os.path.exists(os.path.join(STATE_DIR, canonical_name(login)))


def run(command, stdin_text=None):
    data = None
    if stdin_text is not None:
        data = stdin_text.encode()
    process = subprocess.Popen(command, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = process.communicate(input=data)[0]
    return process.returncode, output.decode("utf-8", "replace")


def create_alias(login, canonical):
    entry = pwd.getpwnam(canonical)
    command = [
        "/usr/sbin/useradd",
        "--no-create-home",
        "--non-unique",
        "--uid", str(entry.pw_uid),
        "--gid", str(entry.pw_gid),
        "--home-dir", entry.pw_dir,
        "--shell", entry.pw_shell,
        "--comment", "digital-examen alias",
        login,
    ]
    return run(command)


def create_user(config, login, password):
    group = config.get("DE_USER_GROUP", "de-students")
    shell = config.get("DE_USER_SHELL", "/bin/bash")
    home = os.path.join(config.get("DE_HOME_BASE", "/home"), login)
    low = config.get("DE_MIN_UID", "20000")
    high = config.get("DE_MAX_UID", "29999")

    command = [
        "/usr/sbin/useradd",
        "--no-create-home",
        "--home-dir", home,
        "--shell", shell,
        "--gid", group,
        "--key", "UID_MIN=" + low,
        "--key", "UID_MAX=" + high,
        "--comment", "digital-examen",
    ]
    extra = config.get("DE_EXTRA_GROUPS", "")
    if extra:
        command = command + ["--groups", extra]
    command.append(login)

    code, output = run(command)
    if code != 0:
        return False, output

    code, output = run(["/usr/sbin/chpasswd"], login + ":" + password + "\n")
    if code != 0:
        run(["/usr/sbin/userdel", "--remove", "--force", login])
        return False, output

    entry = pwd.getpwnam(login)
    size = config.get("DE_HOME_TMPFS_SIZE", "2G")

    if not os.path.isdir(home):
        os.makedirs(home, 0o700)

    options = "size=%s,mode=0700,uid=%d,gid=%d" % (size, entry.pw_uid, entry.pw_gid)
    code, output = run(["/usr/bin/mount", "-t", "tmpfs", "-o", options, "tmpfs", home])
    if code != 0:
        run(["/usr/sbin/userdel", "--remove", "--force", login])
        return False, output

    code, output = run([
        "/usr/bin/rsync", "-a",
        "--chown=%d:%d" % (entry.pw_uid, entry.pw_gid),
        "/etc/skel/", home + "/",
    ])
    if code != 0:
        return False, output

    os.chown(home, entry.pw_uid, entry.pw_gid)
    os.chmod(home, 0o700)
    return True, ""


def set_password(names, password):
    for name in names:
        code, output = run(["/usr/sbin/chpasswd"], name + ":" + password + "\n")
        if code != 0:
            return False, output
    return True, ""


def save_session_mark(canonical, informatics):
    try:
        os.makedirs(RUN_DIR, exist_ok=True)
        path = os.path.join(RUN_DIR, "informatics-" + canonical)
        out = open(path, "w")
        out.write(informatics + "\n")
        out.close()
        os.chmod(path, 0o644)
    except OSError:
        pass


def save_state(canonical, typed, values):
    if not os.path.isdir(STATE_DIR):
        os.makedirs(STATE_DIR, 0o700)
    alias = ""
    if typed != canonical:
        alias = typed
    lines = [
        "login=" + canonical,
        "typed=" + typed,
        "alias=" + alias,
        "uid=" + str(pwd.getpwnam(canonical).pw_uid),
        "created_at=" + str(int(time.time())),
        "user_id=" + str(values.get("user_id", "")),
        "person_id=" + str(values.get("person_id", "")),
        "role=" + str(values.get("role", "")),
        "informatics=" + str(values.get("informatics", "")),
        "offline=" + str(values.get("offline", "")),
    ]
    path = os.path.join(STATE_DIR, canonical)
    out = open(path, "w")
    out.write("\n".join(lines) + "\n")
    out.close()
    os.chmod(path, 0o600)
    save_session_mark(canonical, str(values.get("informatics", "")))


def prepare(config, typed, password, values):
    canonical = canonical_name(typed)

    if not user_exists(canonical):
        done, output = create_user(config, canonical, password)
        if not done:
            return False, "useradd: " + output

    if typed != canonical and not user_exists(typed):
        code, output = create_alias(typed, canonical)
        if code != 0:
            return False, "alias: " + output

    names = [canonical]
    if typed != canonical:
        names.append(typed)
    done, output = set_password(names, password)
    if not done:
        return False, "chpasswd: " + output

    save_state(canonical, typed, values)
    return True, ""
