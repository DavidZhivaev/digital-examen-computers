import json
from pathlib import Path
import re

HOSTS_FILE = Path("/etc/digital-examen/inspect-hosts")
POLICY_FILE = Path("/var/lib/digital-examen/policy.json")
LISTEN_PORT = 3130
MAX_BODY = 131072


def host_name(value):
    return value.lower().rstrip(".")


class CachedFile:
    def __init__(self, path, parse, empty):
        self.path = path
        self.parse = parse
        self.empty = empty
        self.stamp = None
        self.value = empty

    def get(self):
        try:
            stamp = self.path.stat().st_mtime_ns
            if stamp != self.stamp:
                value = self.parse(self.path.read_text())
                self.stamp, self.value = stamp, value
        except FileNotFoundError:
            self.stamp, self.value = None, self.empty
        except (OSError, ValueError):
            pass
        return self.value


def load_hosts(text):
    return frozenset(host for line in text.splitlines() if (
        re.fullmatch(r"[a-z0-9][a-z0-9.-]*\.1580\.ru", host := host_name(line.strip()))
    ))


def load_groups(text):
    return json.loads(text).get("groups", [])


hosts = CachedFile(HOSTS_FILE, load_hosts, frozenset())
groups = CachedFile(POLICY_FILE, load_groups, [])


def matches_content(parts, rules):
    combined = b"\r\n\r\n".join(parts).lower()
    if any(term.lower().encode() in combined for rule in rules for term in rule.get("request_allow", [])):
        return False
    return any(term.lower().encode() in combined for rule in rules for term in rule.get("request_deny", []))


def message_text(first_line, headers, body):
    fields = b"\r\n".join(key + b": " + value for key, value in headers)
    return first_line + b"\r\n" + fields + b"\r\n\r\n" + (body or b"")[:MAX_BODY]



class StreamPrefix:
    def __init__(self, limit, denied):
        self.limit = limit
        self.denied = denied
        self.pending = bytearray()
        self.blocked = None

    def __call__(self, chunk):
        if self.blocked is not None:
            return b"" if self.blocked else chunk
        taken = min(self.limit - len(self.pending), len(chunk))
        self.pending.extend(chunk[:taken])
        if chunk and len(self.pending) < self.limit:
            return b""
        prefix = bytes(self.pending)
        self.pending.clear()
        self.blocked = self.denied(prefix)
        if self.blocked:
            return b""
        return prefix + chunk[taken:]
