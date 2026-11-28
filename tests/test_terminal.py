import importlib.util
import os
from pathlib import Path
import select
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from test_raw_inspection import ROOT, de_common
import de_terminal

spec = importlib.util.spec_from_file_location('terminal_broker_test', ROOT / 'screens' / 'terminal.py')
transport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport)


class PTYTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.owner = 'b' * 64
        directory = self.path / self.owner
        directory.mkdir()
        (directory / 'cwd').write_text(str(self.path))
        self.state = patch.object(de_terminal, 'STATE', self.path)
        self.state.start(); self.addCleanup(self.state.stop)
        self.freeze = patch.object(de_terminal.de_freeze, 'frozen', return_value=False)
        self.freeze.start(); self.addCleanup(self.freeze.stop)
        self.session = self.open()

    def open(self):
        session = de_terminal.Session(dict(session='a' * 64, owner=self.owner, cols=80, rows=24))
        self.addCleanup(session.close)
        session.write("stty -echo; PS1=''; PROMPT_COMMAND=''; printf '\\137\\137READY\\137\\137\\n'\r")
        self.read(session, '__READY__')
        return session

    def read(self, session, expected, timeout=4):
        deadline = time.monotonic() + timeout
        value = b''
        while time.monotonic() < deadline:
            ready, _, _ = select.select([session.fd], [], [], .05)
            if ready:
                value += os.read(session.fd, 8192)
                if expected.encode() in value:
                    return value.decode('utf-8', 'replace')
        self.fail('PTY did not produce %r: %r' % (expected, value[-500:]))

    def test_cd_is_persistent_for_each_operator_and_tab_completes_files(self):
        child = self.path / 'project with spaces'
        child.mkdir()
        (child / 'completion_unique.txt').write_text('TAB_OK\n')
        self.session.write("cd '%s'; printf 'CD_OK\\n'\r" % child)
        self.read(self.session, 'CD_OK')
        self.session.write('cat completion_uni\t\r')
        self.read(self.session, 'TAB_OK')
        self.session.save_cwd()
        self.assertEqual((self.path / self.owner / 'cwd').read_text(), str(child))
        self.session.close()
        restored = self.open()
        restored.write("pwd; printf 'PWD_END\\n'\r")
        self.assertIn(str(child), self.read(restored, 'PWD_END'))
        self.assertFalse((self.path / ('c' * 64) / 'cwd').exists())

    def test_ctrl_c_job_control_and_resize(self):
        self.session.write('sleep 30\r')
        time.sleep(.15)
        self.session.write('\x03')
        self.session.write("printf 'INTERRUPT_OK\\n'\r")
        self.read(self.session, 'INTERRUPT_OK', 2)
        self.session.resize(120, 40)
        self.session.write("stty size; printf 'SIZE_END\\n'\r")
        self.assertIn('40 120', self.read(self.session, 'SIZE_END'))
        self.session.write("id -u; printf 'UID_END\\n'\r")
        self.assertIn(str(os.getuid()), self.read(self.session, 'UID_END'))

    def test_freeze_closes_the_pty_without_sending_output(self):
        with patch.object(de_terminal.de_freeze, 'frozen', return_value=True), \
                patch.object(de_terminal, 'request') as send:
            self.session.read_loop()
            self.assertTrue(self.session.stop.is_set())
            send.assert_not_called()


class TerminalTransportTests(unittest.TestCase):
    def setUp(self):
        self.broker = transport.Broker()
        self.sid, self.state = self.broker.open('2k-418-1', 'b' * 64, 80, 24)

    def test_ownership_and_computer_cannot_be_swapped(self):
        self.broker.get(self.sid, '2k-418-1', 'b' * 64)
        for computer, owner in [('2k-418-2', 'b' * 64), ('2k-418-1', 'c' * 64)]:
            with self.assertRaises(KeyError):
                self.broker.get(self.sid, computer, owner)

    def test_inputs_are_replayed_until_acknowledged_and_outputs_deduplicate(self):
        with self.broker.changed:
            self.broker.input(self.state, dict(data='cd /tmp\r'))
        for _ in range(2):
            batch = self.broker.read(self.sid, '2k-418-1', None, 0, agent=True)
            self.assertEqual(batch['events'][0]['data'], 'cd /tmp\r')
        with self.broker.changed:
            self.broker.input(self.state, dict(data='pwd\r'))
        batch = self.broker.read(self.sid, '2k-418-1', None, 1, agent=True)
        self.assertEqual([event['id'] for event in batch['events']], [2])
        with self.broker.changed:
            self.broker.output(self.state, dict(seq=1, data='/tmp\r\n'))
            self.broker.output(self.state, dict(seq=1, data='/tmp\r\n'))
        batch = self.broker.read(self.sid, '2k-418-1', 'b' * 64, 0)
        self.assertEqual(batch['data'], '/tmp\r\n')
        self.assertEqual(batch['cursor'], 1)

    def test_long_poll_wakes_immediately_and_idle_sessions_expire(self):
        result = []
        reader = threading.Thread(target=lambda: result.append(self.broker.read(self.sid, '2k-418-1', 'b' * 64, 0)))
        reader.start()
        with self.broker.changed:
            self.broker.output(self.state, dict(seq=1, data='hello'))
        reader.join(timeout=1)
        self.assertFalse(reader.is_alive())
        self.assertEqual(result[0]['data'], 'hello')
        self.state['touch'] -= transport.LEASE + 1
        with self.broker.changed:
            self.broker.expire()
        self.assertTrue(self.state['closed'])

    def test_size_and_input_limits_and_closed_sessions(self):
        with self.broker.changed:
            for bad in [dict(data='x'*8193), dict(cols=0, rows=24), dict(cols=80), dict(data='😀', binary=True)]:
                with self.assertRaises(ValueError):
                    self.broker.input(self.state, bad)
            self.state['closed'] = True
            with self.assertRaises(ValueError):
                self.broker.input(self.state, dict(data='whoami\r'))


    def test_output_ring_is_bounded_even_for_empty_batches(self):
        with self.broker.changed:
            for seq in range(1, 4200):
                self.broker.output(self.state, dict(seq=seq, data=''))
        self.assertLessEqual(len(self.state['output']), 4096)
        batch = self.broker.read(self.sid, '2k-418-1', 'b'*64, 0)
        self.assertTrue(batch['truncated'])



class TerminalHTTPTests(unittest.TestCase):
    def test_real_http_round_trip_and_scoped_credentials(self):
        import json
        from http.server import ThreadingHTTPServer
        import urllib.request
        import urllib.error
        sys.path.insert(0, str(ROOT / 'screens'))
        import receiver
        import terminal as wire
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / 'command.secret').write_text('test-command-secret')
            (path / 'proxy.secret').write_text('test-shared-secret')
            secrets = path / 'secrets'; secrets.mkdir()
            (secrets / '2k-418-1.json').write_text(json.dumps(dict(screen='test-device-secret')))
            (secrets / '2k-418-2.json').write_text(json.dumps(dict(screen='other-device-secret')))
            server = ThreadingHTTPServer(('127.0.0.1', 0), receiver.Handler)
            server.daemon_threads = True
            address = 'http://127.0.0.1:%d' % server.server_port
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def call(action, values, secret='test-command-secret', get=False):
                import urllib.parse
                url = address + '/terminal/' + action
                data = None if get else json.dumps(values).encode()
                if get: url += '?' + urllib.parse.urlencode(values)
                req = urllib.request.Request(url, data, {'X-De-Secret': secret, 'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=24) as reply: return json.load(reply)
            try:
                with patch.object(receiver, 'COMMAND_SECRET_FILE', str(path / 'command.secret')), \
                        patch.object(receiver, 'SECRET_FILE', str(path / 'proxy.secret')), \
                        patch.object(receiver, 'SECRETS_DIR', str(secrets)), \
                        patch.object(receiver, 'ALLOW_SHARED', False), \
                        patch.object(receiver, 'QUEUES', {}), \
                        patch.object(wire, 'BROKER', wire.Broker()), \
                        patch.object(de_terminal, 'STATE', path / 'state'), \
                        patch.object(de_common, 'setting', return_value=address), \
                        patch.object(de_common, 'computer', return_value='2k-418-1'), \
                        patch.object(de_common, 'secret', return_value='test-device-secret'), \
                        patch.object(de_terminal.de_freeze, 'frozen', return_value=False):
                    values = dict(computer='2k-418-1', owner='b'*64, cols=80, rows=24)
                    with self.assertRaises(urllib.error.HTTPError) as forbidden:
                        call('open', values, 'test-device-secret')
                    self.assertEqual(forbidden.exception.code, 403)
                    sid = call('open', values)['session']
                    command = receiver.QUEUES['2k-418-1'].pop()
                    de_terminal.open_session(command)
                    scoped = dict(computer='2k-418-1', owner='b'*64, session=sid)
                    with self.assertRaises(urllib.error.HTTPError) as wrong_owner:
                        call('read', dict(scoped, owner='c'*64, after=0), get=True)
                    self.assertEqual(wrong_owner.exception.code, 404)
                    with self.assertRaises(urllib.error.HTTPError) as wrong_device:
                        call('output', dict(computer='2k-418-2', session=sid, seq=1, data='bad'), 'other-device-secret')
                    self.assertEqual(wrong_device.exception.code, 404)
                    cursor = 0
                    started = time.monotonic()
                    call('input', dict(scoped, data="printf '\\137\\137E2E\\137\\137\\n'\r"))
                    text = ''
                    for _ in range(10):
                        batch = call('read', dict(scoped, after=cursor), get=True)
                        text += batch['data']; cursor = batch['cursor']
                        if '__E2E__' in text: break
                    self.assertIn('__E2E__', text)
                    self.assertLess(time.monotonic() - started, 3)
                    call('close', scoped)
                    deadline = time.monotonic() + 2
                    while not de_terminal.SESSIONS[sid].stop.is_set() and time.monotonic() < deadline:
                        time.sleep(.02)
                    self.assertTrue(de_terminal.SESSIONS[sid].stop.is_set())
                    de_terminal.close_all()
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)
