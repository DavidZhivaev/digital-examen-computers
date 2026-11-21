import json
from pathlib import Path
import sys

from de_common import write_file

POLICY = Path("/etc/firefox/policies/policies.json")
STATE = Path("/var/lib/digital-examen/firefox-private-policy.json")
ADDON = "tabs@digital-examen.local"
SETTING = "private_browsing"


def change(install):
    if not install and not STATE.exists():
        return
    existed = POLICY.exists()
    data = json.loads(POLICY.read_text()) if existed else {}
    policies = data.setdefault("policies", {})
    extensions = policies.setdefault("ExtensionSettings", {})
    settings = extensions.setdefault(ADDON, {})
    if install:
        if not STATE.exists():
            previous = {SETTING: settings[SETTING]} if SETTING in settings else {}
            write_file(STATE, json.dumps({"file_existed": existed, "previous": previous}), 0o600)
        settings[SETTING] = True
    else:
        saved = json.loads(STATE.read_text())
        if settings.get(SETTING) is True:
            settings.pop(SETTING)
            settings.update(saved["previous"])
        if not settings:
            extensions.pop(ADDON)
        if not extensions:
            policies.pop("ExtensionSettings")
        if not policies:
            data.pop("policies")
        if not data and not saved["file_existed"]:
            POLICY.unlink(missing_ok=True)
            STATE.unlink()
            return
    write_file(POLICY, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    if not install:
        STATE.unlink()


if __name__ == "__main__":
    try:
        change(sys.argv[1] == "install")
    except (OSError, ValueError, AttributeError, TypeError) as error:
        sys.exit("Firefox: не удалось применить политику приватных окон: " + str(error))
