"""Explicit Linux observation: python tests/manual/copilot_resume.py.

Requires Copilot CLI 1.0.105. Uses disposable homes, COPILOT_OFFLINE, an unused
loopback provider and native SDK stdio RPC. Creates a named session and logs only
metadata: no prompt, model turn, real login, SDK installation or GitHub mutation.

This raw vendor probe shows why Manager must coordinate its own writers.
Two live native SDK processes can resume the same ID and both append metadata.
The same ID is unavailable in a separate home, without copying or redirecting it.
See file_continuation.py for the production session-link and wrapper check.
"""
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid


class Native:
    def __init__(self, base, program, label, home):
        self.base, self.label = base, label
        self.buffer, self.counter, self.log = b'', 0, []
        self.error = (base / (label + '.stderr')).open('wb')
        variables = {'PATH': os.environ['PATH'], 'HOME': str(base / 'user'),
            'TMPDIR': str(base / 'tmp'), 'LANG': 'C.UTF-8', 'COPILOT_HOME': str(home),
            'COPILOT_AUTO_UPDATE': 'false', 'COPILOT_OFFLINE': 'true',
            'COPILOT_PROVIDER_BASE_URL': 'http://127.0.0.1:9/v1',
            'COPILOT_PROVIDER_TYPE': 'openai', 'COPILOT_MODEL': 'fixture',
            'COPILOT_DISABLE_KEYTAR': '1', 'USE_TGREP': 'false'}
        try:
            self.process = subprocess.Popen([program, '--headless', '--stdio', '--no-auto-update',
                '--no-auto-login', '--disable-client-telemetry', '--disable-builtin-mcps',
                '--no-custom-instructions', '--sync-session=off'], cwd=base / 'workspace',
                env=variables, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.error,
                start_new_session=True)
        except BaseException:
            self.error.close()
            raise

    def request(self, method, params):
        self.counter += 1
        payload = json.dumps(dict(jsonrpc='2.0', id=self.counter, method=method, params=params)).encode()
        self.process.stdin.write(('Content-Length: %d\r\n\r\n' % len(payload)).encode() + payload)
        self.process.stdin.flush()
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if b'\r\n\r\n' in self.buffer:
                header, body = self.buffer.split(b'\r\n\r\n', 1)
                length = int(header.split(b':', 1)[1])
                if len(body) >= length:
                    message = json.loads(body[:length])
                    self.buffer = body[length:]
                    self.log.append(message)
                    if message.get('id') == self.counter:
                        return message
                    if 'id' in message and 'method' in message:
                        raise AssertionError('Unexpected server request: ' + str(message))
                    continue
            if select.select([self.process.stdout], [], [], .1)[0]:
                data = os.read(self.process.stdout.fileno(), 65536)
                if not data:
                    raise AssertionError('Native process closed; inspect ' + str(self.base / (self.label + '.stderr')))
                self.buffer += data
        raise AssertionError('Native RPC timeout: ' + method)

    def call(self, method, params):
        response = self.request(method, params)
        if 'error' in response:
            raise AssertionError(str(response['error']))
        return response['result']

    def close(self):
        try:
            if self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=3)
        finally:
            self.process.stdin.close(); self.process.stdout.close(); self.error.close()
            (self.base / (self.label + '.rpc.json')).write_text(json.dumps(self.log, indent=2))


def wait_for_events(path, markers):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        if path.is_file() and all(marker in path.read_text() for marker in markers):
            return
        time.sleep(.05)
    raise AssertionError('Native event log did not persist the fixture metadata: ' + str(path))


def main():
    if sys.platform != 'linux':
        raise SystemExit('This observation is pinned to Linux and Copilot CLI 1.0.105.')
    program = shutil.which('copilot')
    if program is None:
        raise SystemExit('Install Copilot CLI 1.0.105 before running this probe.')
    base = Path(tempfile.mkdtemp(prefix='agent-copilot-resume-check-'))
    print('Disposable fixture and native RPC logs: ' + str(base), flush=True)
    for name in ('source', 'target', 'user', 'tmp', 'workspace'):
        (base / name).mkdir()
    processes = []
    try:
        source = Native(base, program, 'source', base / 'source'); processes.append(source)
        status = source.call('status.get', {})
        if status != {'version': '1.0.105', 'protocolVersion': 3}:
            raise SystemExit('This observation is pinned to Copilot CLI 1.0.105/protocol 3; review other versions separately.')
        identifier = str(uuid.uuid4())
        options = {'sessionId': identifier, 'name': 'disposable history fixture',
            'workingDirectory': str(base / 'workspace'), 'configDirectory': str(base / 'source'),
            'model': 'fixture', 'enableFileHooks': False, 'enableConfigDiscovery': False,
            'disableAllMcpServers': True,
            'provider': {'type': 'openai', 'baseUrl': 'http://127.0.0.1:9/v1'}}
        created = source.call('session.create', options)
        directory = base / 'source/session-state' / identifier
        assert created['sessionId'] == created['nativeSessionId'] == identifier
        assert Path(created['workspacePath']) == directory
        source.call('session.log', {'sessionId': identifier, 'message': 'SOURCE_HISTORY_SENTINEL', 'ephemeral': False})
        events = directory / 'events.jsonl'
        wait_for_events(events, ['SOURCE_HISTORY_SENTINEL'])
        assert (directory / 'workspace.yaml').is_file()

        second = Native(base, program, 'second', base / 'source'); processes.append(second)
        assert second.call('status.get', {}) == status
        options.pop('name')
        options.update(continuePendingWork=False, allowTranscriptRecovery=False)
        resumed = second.call('session.resume', options)
        assert resumed['sessionId'] == resumed['nativeSessionId'] == identifier
        assert Path(resumed['workspacePath']) == directory
        second.call('session.log', {'sessionId': identifier, 'message': 'SECOND_PROCESS_METADATA', 'ephemeral': False})
        source.call('session.log', {'sessionId': identifier, 'message': 'ORIGINAL_PROCESS_METADATA', 'ephemeral': False})
        wait_for_events(events, ['SECOND_PROCESS_METADATA', 'ORIGINAL_PROCESS_METADATA'])
        assert source.process.poll() is None and second.process.poll() is None

        target = Native(base, program, 'target', base / 'target'); processes.append(target)
        options['configDirectory'] = str(base / 'target')
        rejected = target.request('session.resume', options)
        assert 'Session not found' in rejected.get('error', {}).get('message', '')
        assert not (base / 'target/session-state' / identifier).exists()
        print('Observed: both live SDK processes resumed the original ID and appended metadata.', flush=True)
        print('Observed: another home could not resume that ID; no replacement was created.', flush=True)
        print('Standalone processes bypass Manager coordination; see file_continuation.py for the managed adapter check.', flush=True)
    finally:
        for process in reversed(processes):
            process.close()


if __name__ == '__main__':
    main()
