"""Persistent PTYs for individually authorized website operators."""
import atexit
import codecs
import fcntl
import http.client
import json
import os
from pathlib import Path
import pty
import re
import select
import shutil
import signal
import socket
import struct
import termios
import threading
import time
import urllib.parse

import de_common
import de_freeze

IDENTIFIER = re.compile(r'[0-9a-f]{64}')
SESSIONS = {}
LOCK = threading.RLock()
STATE = Path(de_common.STATE_DIR) / 'terminal'


class TerminalGone(OSError):
    pass


TRANSPORT = threading.local()


def request(action, session, payload=None, after=0, connections=None):
    target = urllib.parse.urlsplit(de_common.setting('DE_SCREEN_URL'))
    if target.scheme not in ('http', 'https') or not target.hostname or target.username:
        raise ValueError('Некорректный адрес сервера терминалов')
    values = dict(computer=de_common.computer(), session=session)
    path = target.path.rstrip('/') + '/terminal/' + action
    timeout = 50 if payload is None else 6
    headers = {'X-De-Secret': de_common.secret('screen')}
    body = None
    if payload is None:
        values['after'] = after
        path += '?' + urllib.parse.urlencode(values)
    else:
        values.update(payload)
        body = json.dumps(values).encode()
        headers['Content-Type'] = 'application/json'
    origin = target.scheme, target.hostname, target.port
    connection = getattr(TRANSPORT, 'connection', None)
    if connection is None or getattr(TRANSPORT, 'origin', None) != origin:
        if connection is not None:
            connection.close()
        factory = http.client.HTTPSConnection if target.scheme == 'https' else http.client.HTTPConnection
        connection = factory(target.hostname, target.port, timeout=timeout)
        TRANSPORT.connection, TRANSPORT.origin = connection, origin
    if connections is not None:
        connections.add(connection)
    try:
        connection.request('GET' if payload is None else 'POST', path, body, headers)
        response = connection.getresponse()
        data = response.read(2 * 1024 * 1024)
        if response.status in (400, 401, 403, 404, 409):
            raise TerminalGone('Сеанс терминала завершён')
        if response.status != 200:
            raise OSError('Сеанс терминала недоступен: HTTP %d' % response.status)
        return json.loads(data.decode('utf-8'))
    except (OSError, ValueError, http.client.HTTPException) as error:
        connection.close()
        if connections is not None:
            connections.discard(connection)
        TRANSPORT.connection = None
        if isinstance(error, http.client.HTTPException):
            raise OSError('Соединение терминала прервано') from error
        raise


class Session:
    def __init__(self, command):
        self.sid, self.owner = command['session'], command['owner']
        if not IDENTIFIER.fullmatch(self.sid) or not IDENTIFIER.fullmatch(self.owner):
            raise ValueError('Некорректный идентификатор терминала')
        self.directory = STATE / self.owner
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.cwd = (self.directory / 'cwd').read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            self.cwd = ''
        if not self.cwd.startswith('/') or not os.path.isdir(self.cwd):
            self.cwd = os.path.expanduser('~')
        self.stop = threading.Event()
        self.connections = set()
        self.close_lock = threading.Lock()
        self.input_after = int(command.get('input_after', 0))
        self.output_after = int(command.get('output_after', 0))
        cols, rows = self.dimensions(command['cols'], command['rows'])
        size = struct.pack('HHHH', rows, cols, 0, 0)
        env = dict(os.environ, TERM='xterm-256color', COLORTERM='truecolor',
                   HISTFILE=str(self.directory / 'history'))
        binary = shutil.which('bash') or '/bin/bash'
        pid, fd = pty.fork()
        if pid == 0:
            try:
                os.chdir(self.cwd)
                fcntl.ioctl(0, termios.TIOCSWINSZ, size)
                os.execve(binary, [binary, '--login', '-i'], env)
            except OSError:
                os.write(2, b'Cannot start terminal shell\r\n')
                os._exit(127)
        self.pid, self.fd = pid, fd
        os.set_blocking(fd, False)

    @staticmethod
    def dimensions(cols, rows):
        if type(cols) is not int or type(rows) is not int or not 2 <= cols <= 500 or not 2 <= rows <= 200:
            raise ValueError('Некорректный размер терминала')
        return cols, rows

    def resize(self, cols, rows):
        cols, rows = self.dimensions(cols, rows)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))

    def save_cwd(self):
        try:
            cwd = os.readlink('/proc/%d/cwd' % self.pid)
            if cwd != self.cwd and os.path.isdir(cwd):
                de_common.write_file(str(self.directory / 'cwd'), cwd, 0o600)
                self.cwd = cwd
            elif not (self.directory / 'cwd').exists():
                de_common.write_file(str(self.directory / 'cwd'), cwd, 0o600)
        except OSError:
            pass

    def write(self, text):
        pending = text if isinstance(text, bytes) else text.encode('utf-8')
        while pending and not self.stop.is_set() and not de_freeze.frozen():
            _, ready, _ = select.select([], [self.fd], [], .2)
            if ready:
                try:
                    pending = pending[os.write(self.fd, pending):]
                except BlockingIOError:
                    continue

    def emit(self, data, closed=False):
        self.output_after += 1
        payload = dict(seq=self.output_after, data=data, closed=closed)
        deadline = time.monotonic() + 30
        while not self.stop.is_set() and not de_freeze.frozen() and time.monotonic() < deadline:
            try:
                return request('output', self.sid, payload, connections=self.connections).get('ok') is True
            except TerminalGone:
                return False
            except (OSError, ValueError):
                self.stop.wait(.5)
        return False

    def read_loop(self):
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        try:
            if self.output_after:
                self.emit('\r\n\x1bc')
            while not self.stop.is_set() and not de_freeze.frozen():
                batch_started = time.monotonic()
                ready, _, _ = select.select([self.fd], [], [], .5)
                if not ready:
                    continue
                try:
                    data = os.read(self.fd, 8192)
                except BlockingIOError:
                    continue
                except OSError:
                    break
                if not data:
                    break
                self.save_cwd()
                text = decoder.decode(data)
                if text and not self.emit(text):
                    return
                # Backpressure caps a noisy command at about 160 KiB/s without dropping bytes.
                self.stop.wait(max(0, .05 - (time.monotonic() - batch_started)))
            if not de_freeze.frozen():
                self.emit(decoder.decode(b'', final=True), closed=True)
        except (OSError, ValueError):
            pass
        finally:
            self.close()

    def input_loop(self):
        last_contact = time.monotonic()
        try:
            while not self.stop.is_set() and not de_freeze.frozen():
                try:
                    answer = request('poll', self.sid, after=self.input_after, connections=self.connections)
                    last_contact = time.monotonic()
                    if answer.get('closed'):
                        break
                    for event in answer.get('events', []):
                        if event['id'] <= self.input_after:
                            continue
                        if event.get('cols') is not None:
                            self.resize(event['cols'], event['rows'])
                        data = event.get('data', '')
                        self.write(data.encode('latin-1') if event.get('binary') else data)
                        self.input_after = event['id']
                except TerminalGone:
                    break
                except (OSError, ValueError, KeyError, TypeError):
                    if time.monotonic() - last_contact > 60:
                        break
                    self.stop.wait(1)
        finally:
            self.close()

    def start(self):
        threading.Thread(target=self.read_loop, daemon=True).start()
        threading.Thread(target=self.input_loop, daemon=True).start()

    def close(self):
        with self.close_lock:
            if self.stop.is_set():
                return
            self.save_cwd()
            self.stop.set()
            for connection in tuple(self.connections):
                try:
                    if connection.sock is not None:
                        connection.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()
            self.connections.clear()
            try:
                os.killpg(self.pid, signal.SIGHUP)
            except ProcessLookupError:
                pass
            try:
                os.close(self.fd)
            except OSError:
                pass
            # Reap asynchronously so freeze never waits on an interactive program.
            threading.Thread(target=self.reap, daemon=True).start()

    def reap(self):
        try:
            for _ in range(20):
                if os.waitpid(self.pid, os.WNOHANG)[0]:
                    return
                time.sleep(.05)
            os.killpg(self.pid, signal.SIGKILL)
            os.waitpid(self.pid, 0)
        except (ChildProcessError, ProcessLookupError):
            pass


def open_session(command):
    with LOCK:
        for sid, session in list(SESSIONS.items()):
            if session.stop.is_set():
                del SESSIONS[sid]
        if de_freeze.frozen():
            return
        sid = command.get('session')
        if sid in SESSIONS:
            if SESSIONS[sid].owner != command.get('owner'):
                raise ValueError('Владелец терминала изменился')
            SESSIONS[sid].resize(command['cols'], command['rows'])
            return
        for old_sid, old in list(SESSIONS.items()):
            if old.owner == command.get('owner'):
                old.close()
                del SESSIONS[old_sid]
        if len(SESSIONS) >= 8:
            raise ValueError('Слишком много терминалов компьютера')
        session = Session(command)
        SESSIONS[sid] = session
        session.start()


def close_all():
    with LOCK:
        for session in SESSIONS.values():
            session.close()
        SESSIONS.clear()


atexit.register(close_all)
