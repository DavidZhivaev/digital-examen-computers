import os
import re
import tempfile

import de_common

COMMANDS = {"reboot", "git pull", "scp"}
SCP_CONFIG = "/etc/digital-examen/mass-scp.conf"


def copy_package():
    source = de_common.setting("DE_MASS_SCP_SOURCE", "")
    if not re.fullmatch(r"de-package:/[a-zA-Z0-9_./-]+", source):
        return 2, "Источник scp задаётся в DE_MASS_SCP_SOURCE"
    if not os.path.isfile(SCP_CONFIG):
        return 2, "Отсутствует конфигурация SSH для de-package"
    directory = os.path.join(de_common.STATE_DIR, "packages")
    os.makedirs(directory, mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory) as staging:
        target = os.path.join(staging, "download")
        code, output = de_common.run(
            ["scp", "-F", SCP_CONFIG, "-B", "-o", "StrictHostKeyChecking=yes",
             "-o", "ConnectTimeout=10", "--", source, target], 120,
        )
        if code:
            return code, output
        os.chmod(target, 0o600)
        os.replace(target, os.path.join(directory, "latest"))
    return 0, "Файл загружен в packages/latest"


def execute(command, target_id):
    if command not in COMMANDS:
        return 126, "Команда не разрешена для массового выполнения"
    if command == "scp":
        return copy_package()
    if command == "git pull":
        code, output = de_common.run([
            "systemd-run", "--unit=de-mass-update-%d" % target_id, "--collect",
            "--property=RuntimeMaxSec=18min", "/usr/local/sbin/de-update", "--report", str(target_id),
        ])
        return (code, output) if code else (None, "")
    code, output = de_common.run([
        "systemd-run", "--on-active=20s", "--collect", "systemctl", "reboot",
    ])
    return code, output or "Перезагрузка запланирована"
