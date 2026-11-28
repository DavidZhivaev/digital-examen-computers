"""Bounded interactive terminal transport, authenticated by the existing receiver."""
from collections import deque
import re
import secrets
import threading
import time

ID = re.compile(r"[0-9a-f]{64}")
LIMIT = 1024 * 1024
LEASE = 90


class Broker:
    def __init__(self):
        self.changed = threading.Condition()
        self.sessions = {}

    def expire(self):
        now = time.monotonic()
        for sid, state in list(self.sessions.items()):
            if now - state['touch'] > LEASE:
                state['closed'] = True
            if now - state['touch'] > LEASE * 2:
                del self.sessions[sid]

    def open(self, computer, owner, cols, rows):
        with self.changed:
            self.expire()
            for sid, state in self.sessions.items():
                if (state['computer'], state['owner']) == (computer, owner) and not state['closed']:
                    state['touch'] = time.monotonic()
                    return sid, state
            if len(self.sessions) >= 64:
                raise ValueError('Слишком много открытых терминалов')
            sid = secrets.token_hex(32)
            state = dict(computer=computer, owner=owner, touch=time.monotonic(), closed=False,
                         ready=False, input=deque(), input_id=0, input_size=0,
                         output=deque(), output_id=0, cursor=0, output_size=0)
            self.sessions[sid] = state
            return sid, state

    def get(self, sid, computer, owner=None):
        self.expire()
        state = self.sessions.get(sid)
        if not state or state['computer'] != computer or (owner is not None and state['owner'] != owner):
            raise KeyError('Терминал не найден')
        return state

    def input(self, state, payload):
        data = payload.get('data', '')
        cols, rows = payload.get('cols'), payload.get('rows')
        binary = payload.get('binary', False)
        if (not isinstance(data, str) or len(data) > 8192 or type(binary) is not bool
                or (binary and any(ord(char) > 255 for char in data))
                or ((cols is None) != (rows is None))
                or (cols is not None and (type(cols) is not int or type(rows) is not int
                                         or not 2 <= cols <= 500 or not 2 <= rows <= 200))):
            raise ValueError('Некорректный ввод терминала')
        if state['closed']:
            raise ValueError('Сеанс завершён')
        size = len(data.encode('utf-8'))
        if state['input_size'] + size > 65536 or len(state['input']) >= 512:
            raise ValueError('Компьютер не успевает принимать ввод')
        if data or cols is not None:
            state['input_id'] += 1
            state['input'].append(dict(id=state['input_id'], data=data, binary=binary, cols=cols, rows=rows))
            state['input_size'] += size
        state['touch'] = time.monotonic()
        self.changed.notify_all()

    def output(self, state, payload):
        seq, data = payload.get('seq'), payload.get('data', '')
        if type(seq) is not int or not isinstance(data, str) or len(data) > 65536:
            raise ValueError('Некорректный вывод терминала')
        if state['closed']:
            return
        if seq <= state['output_id']:
            return  # Retrying the same batch must not duplicate terminal output.
        if seq != state['output_id'] + 1:
            raise ValueError('Нарушена последовательность вывода')
        state['output_id'] = seq
        state['cursor'] += 1
        state['output'].append((state['cursor'], data))
        state['output_size'] += len(data.encode('utf-8'))
        while state['output_size'] > LIMIT or len(state['output']) > 4096:
            _, removed = state['output'].popleft()
            state['output_size'] -= len(removed.encode('utf-8'))
        state['ready'] = True
        if payload.get('closed') is True:
            state['closed'] = True
        self.changed.notify_all()

    def read(self, sid, computer, owner, after, agent=False):
        with self.changed:
            state = self.get(sid, computer, None if agent else owner)
            if not agent:
                state['touch'] = time.monotonic()
            deadline = time.monotonic() + 18
            while True:
                self.expire()
                if agent:
                    while state['input'] and state['input'][0]['id'] <= after:
                        removed = state['input'].popleft()
                        state['input_size'] -= len(removed['data'].encode('utf-8'))
                    if state['input'] or state['closed']:
                        return dict(events=list(state['input']), closed=state['closed'])
                elif state['cursor'] > after or state['closed']:
                    items = [(seq, text) for seq, text in state['output'] if seq > after]
                    return dict(data=''.join(text for _, text in items), cursor=state['cursor'],
                                closed=state['closed'], ready=state['ready'],
                                truncated=bool(state['output'] and after < state['output'][0][0] - 1))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return dict(events=[], closed=False) if agent else dict(
                        data='', cursor=state['cursor'], closed=False, ready=state['ready'])
                self.changed.wait(min(remaining, 1))


BROKER = Broker()


def handle(handler, route, query, post, queue_command, safe_name):
    if not route.startswith('/terminal/'):
        return False
    action = route.rsplit('/', 1)[-1]
    try:
        payload = handler.body_json(128 * 1024) if post else {k: v[0] for k, v in query.items()}
        if not isinstance(payload, dict):
            raise ValueError('Некорректный запрос')
        computer = safe_name(payload.get('computer', ''))
        if not computer:
            raise ValueError('Некорректный компьютер')
        agent = action in {'poll', 'output'}
        if not (handler.allowed_computer(computer) if agent else handler.allowed(True)):
            handler.reply(403, {'error': 'Нет доступа к терминалу'})
            return True
        owner = payload.get('owner', '')
        if not agent and (not isinstance(owner, str) or not ID.fullmatch(owner)):
            raise ValueError('Некорректный владелец')
        if action == 'open' and post:
            cols, rows = payload.get('cols', 80), payload.get('rows', 24)
            if type(cols) is not int or type(rows) is not int or not 2 <= cols <= 500 or not 2 <= rows <= 200:
                raise ValueError('Некорректный размер терминала')
            with BROKER.changed:
                sid, state = BROKER.open(computer, owner, cols, rows)
                command = dict(name='terminal-open', session=sid, owner=owner, cols=cols, rows=rows,
                               input_after=state['input_id'], output_after=state['output_id'])
            queue_command(computer, command)
            handler.reply(200, dict(session=sid))
            return True
        sid = payload.get('session', '')
        if not isinstance(sid, str) or not ID.fullmatch(sid):
            raise ValueError('Некорректный сеанс')
        if action in {'read', 'poll'} and not post:
            after = int(payload.get('after', 0))
            if after < 0:
                raise ValueError('Некорректная позиция')
            handler.reply(200, BROKER.read(sid, computer, owner, after, agent))
            return True
        with BROKER.changed:
            state = BROKER.get(sid, computer, None if agent else owner)
            if action == 'input' and post:
                BROKER.input(state, payload)
            elif action == 'output' and post:
                BROKER.output(state, payload)
            elif action == 'close' and post:
                state['closed'] = True
                BROKER.changed.notify_all()
            else:
                raise ValueError('Неизвестная операция терминала')
        handler.reply(200, {'ok': True})
    except KeyError:
        handler.reply(404, {'error': 'Сеанс терминала завершён'})
    except (ValueError, TypeError) as error:
        handler.reply(400, {'error': str(error)})
    return True
