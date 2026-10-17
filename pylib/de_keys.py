import os

MEDIA_DIRS = ("/run/media", "/media", "/mnt")


def find_key(name="de-key.txt"):
    for base in MEDIA_DIRS:
        if not os.path.isdir(base):
            continue
        try:
            tops = sorted(os.listdir(base))
        except OSError:
            continue
        for first in tops:
            top = os.path.join(base, first)
            candidate = os.path.join(top, name)
            if os.path.isfile(candidate):
                return candidate
            if not os.path.isdir(top):
                continue
            try:
                inner = sorted(os.listdir(top))
            except OSError:
                continue
            for second in inner:
                candidate = os.path.join(top, second, name)
                if os.path.isfile(candidate):
                    return candidate
    return ""


def parse_key(path):
    values = {}
    try:
        for line in open(path):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    except IOError:
        return {}
    return values
