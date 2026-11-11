"""Explicit native storage check: python tests/manual/codex_cross_profile.py.

Requires Linux, bubblewrap, aixand-agent-manager[sdk] and the qualified Codex CLI. The
fixture layout was verified with Codex 0.162.1 and 0.166.0. Uses disposable homes, ephemeral
credentials and stdio RPC; never sends a model turn or reads a real login. This
checks native history/locking, not the interactive terminal or a live account.
Fixture files and native diagnostics remain under the printed /tmp directory.
"""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import queue
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent_manager.manager import Manager
from agent_manager.profiles.sessions import find
from agent_manager.runtime.native.codex_continuation import prepare
from agent_manager.runtime.native.codex_continue import native_command


CONFIG = '''cli_auth_credentials_store = "ephemeral"
check_for_update_on_startup = false
model_provider = "fixture"
model = "fixture-model"
[model_providers.fixture]
name = "Offline fixture"
base_url = "http://127.0.0.1:9/v1"
wire_api = "responses"
requires_openai_auth = false
'''


class Wire:
    def __init__(self, base, program, home, store=None, prepared=None):
        command = [program, 'app-server', '--listen', 'stdio://']
        if prepared is not None:
            descriptor = json.loads(Path(prepared[-1]).read_text())
            # Use the product's mount and native launch command, changing only
            # the transport. No socket permissions or TUI are needed here.
            command = [*prepared[:prepared.index('--') + 1], *native_command(descriptor, 'stdio://')]
        for name in ('user', 'tmp'):
            (base / name).mkdir(exist_ok=True)
        user = base / 'user'
        env = {'PATH': os.environ['PATH'], 'HOME': str(user), 'LANG': 'C.UTF-8',
               'CODEX_HOME': str(home), 'TMPDIR': str(base / 'tmp'),
               'XDG_CONFIG_HOME': str(user / 'config'), 'XDG_DATA_HOME': str(user / 'data'),
               'XDG_CACHE_HOME': str(user / 'cache')}
        if store is not None:
            env['CODEX_SQLITE_HOME'] = str(store)
        self.log = (base / (home.name + ('-shared' if store else '-own') + '.log')).open('ab')
        self.process = subprocess.Popen(command, env=env, cwd=base / 'workspace',
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, text=True, bufsize=1)
        self.messages = queue.Queue()
        self.counter = 0

        def read():
            try:
                for line in self.process.stdout:
                    try:
                        self.messages.put(json.loads(line))
                    except ValueError:
                        continue
            finally:
                self.messages.put(None)

        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()

    def __enter__(self):
        try:
            self.request('initialize', {'clientInfo': {'name': 'agent_manager', 'version': '0.1.0'},
                'capabilities': {'experimentalApi': True}})
            self.process.stdin.write(json.dumps({'method': 'initialized'}) + '\n')
            self.process.stdin.flush()
            return self
        except BaseException:
            self.__exit__()
            raise

    def request(self, method, params):
        self.counter += 1
        identifier = self.counter
        self.process.stdin.write(json.dumps({'id': identifier, 'method': method, 'params': params}) + '\n')
        self.process.stdin.flush()
        deadline = time.monotonic() + 15
        while True:
            message = self.messages.get(timeout=max(.001, deadline - time.monotonic()))
            if message is None:
                raise RuntimeError('Native process ended; inspect ' + self.log.name)
            if time.monotonic() > deadline:
                raise TimeoutError(method)
            if message.get('id') != identifier or 'method' in message:
                continue
            if 'error' in message:
                raise RuntimeError(json.dumps(message['error']))
            return message['result']

    def __exit__(self, *_):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.reader.join(timeout=1)
        self.process.stdout.close()
        self.log.close()


def inject(wire, thread_id, text):
    wire.request('thread/inject_items', {'threadId': thread_id, 'items': [{
        'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': text}]}]})


def seed_turns(base, rollout):
    """Write synthetic completed turns; native Codex builds their projection."""
    records = [json.loads(line) for line in rollout.read_text().splitlines()]
    context = next((row['payload'] for row in reversed(records) if row['type'] == 'turn_context'),
        {'cwd': str(base / 'workspace'), 'approval_policy': 'on-request',
         'sandbox_policy': {'type': 'read-only'}, 'model': 'fixture-model', 'summary': 'none'})

    def append(kind, payload):
        records.append({'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'ordinal': len(records), 'type': kind, 'payload': payload})

    for index in (1, 2):
        turn = 'fixture-turn-' + str(index)
        message, reply = 'SOURCE_HISTORY_' + str(index), 'SOURCE_REPLY_' + str(index)
        append('event_msg', {'type': 'task_started', 'turn_id': turn, 'model_context_window': 128000})
        append('turn_context', {**context, 'turn_id': turn})
        append('response_item', {'type': 'message', 'role': 'user',
            'content': [{'type': 'input_text', 'text': message}]})
        append('event_msg', {'type': 'user_message', 'message': message, 'images': [], 'text_elements': []})
        append('response_item', {'type': 'message', 'role': 'assistant', 'phase': 'final_answer',
            'content': [{'type': 'output_text', 'text': reply}]})
        append('event_msg', {'type': 'agent_message', 'message': reply, 'phase': 'final_answer'})
        append('event_msg', {'type': 'task_complete', 'turn_id': turn, 'last_agent_message': reply})
    rollout.write_text(''.join(json.dumps(row) + '\n' for row in records))


def seed_items(source, thread_id):
    """Fixture-only SQLite writes. Production never creates or edits these rows."""
    with sqlite3.connect(source / 'thread_history_1.sqlite') as db:
        for index in (1, 2):
            turn = 'fixture-turn-' + str(index)
            ordinal = db.execute('SELECT rollout_ordinal FROM thread_turns WHERE thread_id=? AND turn_id=?',
                (thread_id, turn)).fetchone()[0]
            items = [
                {'id': 'fixture-user-' + str(index), 'type': 'userMessage', 'content': [
                    {'type': 'text', 'text': 'SOURCE_HISTORY_' + str(index), 'text_elements': []}]},
                {'id': 'fixture-agent-' + str(index), 'type': 'agentMessage',
                    'text': 'SOURCE_REPLY_' + str(index), 'phase': 'final_answer'},
            ]
            for position, item in enumerate(items):
                db.execute('INSERT INTO thread_items(thread_id,turn_id,item_id,rollout_ordinal,created_at_ms,item_json,item_type) VALUES(?,?,?,?,?,?,?)',
                    (thread_id, turn, item['id'], ordinal + position + 2, 1, json.dumps(item), item['type']))


def main():
    program = shutil.which('codex')
    if program is None or shutil.which('bwrap') is None:
        raise SystemExit('Install the qualified Codex CLI and bubblewrap before running this check.')
    program = str(Path(program).resolve())
    base = Path(tempfile.mkdtemp(prefix='agent-codex-history-check-'))
    print('Disposable fixture and logs: ' + str(base), flush=True)
    (base / 'workspace').mkdir()
    source, target = base / 'source', base / 'target'
    for home in (source, target):
        home.mkdir()
        (home / 'config.toml').write_text(CONFIG.replace('fixture-model', 'fixture-' + home.name))
    with Wire(base, program, source) as wire:
        thread = wire.request('thread/start', {'cwd': str(base / 'workspace'), 'historyMode': 'paginated'})['thread']
        thread_id = thread['id']
        inject(wire, thread_id, 'SOURCE_HISTORY_INITIAL')
    rollout = Path(thread['path'])
    seed_turns(base, rollout)
    with Wire(base, program, source) as wire:
        wire.request('thread/resume', {'threadId': thread_id, 'excludeTurns': True})
        turns = wire.request('thread/turns/list', {'threadId': thread_id, 'limit': 5, 'itemsView': 'full'})
        assert len(turns['data']) == 2
    seed_items(source, thread_id)
    with Wire(base, program, target) as wire:
        unrelated = wire.request('thread/start', {'cwd': str(base / 'workspace'), 'historyMode': 'paginated'})['thread']['id']
        inject(wire, unrelated, 'UNRELATED_TARGET_HISTORY')
    preferences = base / 'providers.toml'
    preferences.write_text('')
    manager = Manager('codex', config=preferences, root=base / 'manager')
    manager.connect_profile('source', source)
    manager.connect_profile('target', target)
    view = prepare(manager, target, find(manager, thread_id), args=['resume', thread_id], cwd=base / 'workspace')
    prepared = view.command(manager, [program, 'resume', thread_id])
    with Wire(base, program, target, source, prepared) as wire:
        config = wire.request('config/read', {'cwd': str(base / 'workspace'), 'includeLayers': False})['config']
        assert Path(config['sqlite_home']) == source
        assert config['model'] == 'fixture-target'
        resumed = wire.request('thread/resume', {'threadId': thread_id, 'path': str(rollout),
            'cwd': str(base / 'workspace'), 'excludeTurns': True})['thread']
        assert resumed['id'] == thread_id and Path(resumed['path']) == rollout
        items = wire.request('thread/items/list', {'threadId': thread_id, 'limit': 10})
        assert len(items['data']) == 4
        assert all('SOURCE_HISTORY_' + str(index) in json.dumps(items) for index in (1, 2))
        with (source / 'thread-writer-locks' / (thread_id + '.lock')).open('rb') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                raise AssertionError('The native source writer lock was not acquired')
        inject(wire, thread_id, 'DESTINATION_HISTORY_ADDITION')
    with Wire(base, program, source) as wire:
        wire.request('thread/resume', {'threadId': thread_id, 'excludeTurns': True})
        assert len(wire.request('thread/items/list', {'threadId': thread_id, 'limit': 10})['data']) == 4
    assert 'DESTINATION_HISTORY_ADDITION' in rollout.read_text()
    for home, expected in ((source, thread_id), (target, unrelated)):
        with sqlite3.connect(home / 'state_5.sqlite') as db:
            assert [row[0] for row in db.execute('SELECT id FROM threads')] == [expected]
    print('PASS: target configuration, original identity, paginated reads, source writer lock, '
          'write-back and unrelated destination history.', flush=True)


if __name__ == '__main__':
    main()
