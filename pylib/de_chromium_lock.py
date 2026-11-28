#!/usr/bin/python3.8

import os
import errno
from pathlib import Path
import pwd
import socket
import subprocess
import sys


def clear_stale_lock(home):
    profile = Path(home) / ".config/chromium"
    lock = profile / "SingletonLock"
    try:
        original = os.readlink(lock)
        host, separator, pid = original.rpartition("-")
        if not separator or not host or host == socket.gethostname() or not pid.isdecimal():
            return
        if int(pid) <= 1:
            return
        try:
            os.kill(int(pid), 0)
            return
        except ProcessLookupError:
            pass
        # A crashed browser can leave a Unix socket inode behind. Only a
        # listener proves that the profile is still in use.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(.2)
            try:
                probe.connect(str(profile / "SingletonSocket"))
                return
            except OSError as error:
                if error.errno not in (errno.ENOENT, errno.ECONNREFUSED):
                    return
        if os.readlink(lock) == original:
            lock.unlink()
    except (OSError, OverflowError, ValueError):
        return


def main():
    if len(sys.argv) == 2 and os.geteuid() != 0:
        clear_stale_lock(sys.argv[1])
        return
    if os.geteuid() != 0:
        return
    for user in pwd.getpwall():
        if user.pw_uid == 0 or not user.pw_dir.startswith("/home/"):
            continue
        local = subprocess.run(["findmnt", "-n", "-o", "FSTYPE", "-T", user.pw_dir],
                               text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if local.stdout.strip() not in {"btrfs", "ext4", "xfs"}:
            continue
        subprocess.run(["runuser", "-u", user.pw_name, "--", "/usr/bin/python3.8",
                        "/usr/local/lib/digital-examen/de_chromium_lock.py", user.pw_dir], check=False)


if __name__ == "__main__":
    main()
