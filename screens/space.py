import os
import shutil
import subprocess
import sys


def days(data_dir):
    if not os.path.isdir(data_dir):
        return []
    out = []
    for name in sorted(os.listdir(data_dir)):
        path = os.path.join(data_dir, name)
        if not os.path.isdir(path) or len(name) != 10 or name.count("-") != 2:
            continue
        out.append(name)
    return out


def records(data_dir, day):
    out = []
    base = os.path.join(data_dir, day)
    if not os.path.isdir(base):
        return out
    for computer in sorted(os.listdir(base)):
        room = os.path.join(base, computer)
        if not os.path.isdir(room):
            continue
        for login in sorted(os.listdir(room)):
            folder = os.path.join(room, login)
            if os.path.isdir(folder):
                out.append(folder)
    return out


def folder_size(path):
    total = 0
    for root, _, names in os.walk(path):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def free_bytes(data_dir):
    try:
        return shutil.disk_usage(data_dir).free
    except OSError:
        return 0


def remove(path):
    size = folder_size(path)
    subprocess.call(["rm", "-rf", path])
    return size


def free_up(data_dir, need_bytes, say=None, tell_day=None):
    """Освободить место, удаляя самое старое.

    Сначала уходят целые старые дни. Если остался один день, внутри него
    удаляются самые старые записи. Новая запись не теряется никогда:
    удаляется только то, что лежит дольше всех.
    """
    talk = say or (lambda message: sys.stdout.write(message + "\n"))
    dropped = []

    while free_bytes(data_dir) < need_bytes:
        left = days(data_dir)
        if not left:
            break
        if len(left) > 1:
            oldest = left[0]
            size = remove(os.path.join(data_dir, oldest))
            talk("мало места: удалён день %s, освобождено %d байт" % (oldest, size))
            if tell_day is not None:
                tell_day(oldest)
            dropped.append(oldest)
            continue

        folders = records(data_dir, left[0])
        if not folders:
            break
        oldest_folder = min(folders, key=lambda item: os.path.getmtime(item))
        size = remove(oldest_folder)
        talk("мало места: удалена запись %s, освобождено %d байт" % (oldest_folder, size))
        dropped.append(oldest_folder)

    return dropped
