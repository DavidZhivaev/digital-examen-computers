import json
import os
from pathlib import Path
import pwd
import socket
import socketserver
import struct
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import de_block_history
import de_common

RUN = Path(de_common.RUN_DIR)
SOCKET = RUN / "control.sock"


class Control(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        try:
            peer = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            _, uid, _ = struct.unpack("3i", peer)
            message = json.loads(self.rfile.readline(8193))
            action = message.get("action")
            if action == "history":
                rows = de_block_history.read()
                if not de_common.is_admin(pwd.getpwuid(uid).pw_name):
                    rows = [[stamp, urlsplit(url).netloc, reason, group, trigger]
                            for stamp, url, reason, group, trigger in rows]
                result = {"history": rows}
            elif action in ("block", "screen") and message.get("mode") in ("on", "off"):
                host = de_common.setting("DE_API_URL").rstrip("/")
                if urlsplit(host).scheme != "https":
                    raise ValueError("Вход оператора требует настроенный HTTPS-сайт")
                request = Request(host + "/api/computers/cli/computers/" + de_common.computer())
                request.add_unredirected_header("Authorization", "Bearer " + message["token"])
                with urlopen(request, timeout=5) as answer:
                    if json.load(answer).get("name") != de_common.computer():
                        raise ValueError("Сайт не подтвердил этот компьютер")
                flag = RUN / (action + "-off")
                if message["mode"] == "off":
                    flag.touch(mode=0o600)
                else:
                    flag.unlink(missing_ok=True)
                result = {"mode": message["mode"]}
            else:
                raise ValueError("Неизвестная команда")
        except (OSError, ValueError, KeyError, AttributeError):
            result = {"error": "Операция не выполнена. Проверьте доступность сайта и права оператора."}
        self.wfile.write(json.dumps(result, ensure_ascii=False).encode() + b"\n")


def listen():
    RUN.mkdir(mode=0o755, parents=True, exist_ok=True)
    SOCKET.unlink(missing_ok=True)
    with socketserver.UnixStreamServer(str(SOCKET), Control) as server:
        os.chmod(SOCKET, 0o666)
        server.serve_forever()
