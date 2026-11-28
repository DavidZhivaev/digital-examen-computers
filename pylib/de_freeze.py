"""Suspend Digital Examen without touching school-managed network settings."""
from pathlib import Path
import subprocess
import threading

FLAG = Path("/var/lib/digital-examen/frozen")
LOCK = threading.RLock()
UNITS = (
    "de-screen.service", "de-net.service", "de-inspect.service", "de-traffic.service",
    "de-health.timer", "de-rotate.timer", "de-inspect-ca.timer", "de-update.timer",
    "de-update-request.socket", "de-health.service", "de-rotate.service", "de-inspect-ca.service",
)
RESUME = UNITS[:9]
_applied = None


def frozen():
    try:
        return FLAG.exists()
    except PermissionError:
        return True


def apply(value):
    import de_common
    global _applied
    with LOCK:
        if value:
            if not frozen():
                de_common.write_file(str(FLAG), "1", 0o600)
        else:
            FLAG.unlink(missing_ok=True)
        if _applied == value:
            return
        # Service conditions also keep health checks and reboot from restarting capture.
        result = subprocess.run(
            ["systemctl", "--no-block", "stop" if value else "start", *(UNITS if value else RESUME)],
            timeout=3, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            _applied = value
