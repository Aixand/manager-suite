"""Explicit Linux probe: python tests/manual/claude_resume.py.

Requires native Claude Code 2.1.316. Uses synthetic transcripts, isolated homes,
an offline dummy API key and only the built-in /rename command. No model turn,
real login or user configuration is used. Logs stay in the printed /tmp directory.

This raw vendor probe shows why Manager must coordinate its own writers:
path-based resume writes back correctly, but two standalone interactive processes
can open the same session and both append metadata. The production adapter's
separate check is in file_continuation.py.
"""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import termios
import tempfile
import time
import uuid


class NativeTerminal:
    def __init__(self, base, program, label, home, resume, *, command=None, extra_env=None):
        self.base, self.home, self.label = base, home, label
        self.output = bytearray()
        self.approved = False
        self.wrapped = command is not None
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 45, 140, 0, 0))
        variables = {'PATH': os.environ['PATH'], 'HOME': str(base / 'user'), 'LANG': 'C.UTF-8',
            'TERM': 'xterm-256color', 'TMPDIR': str(base / 'tmp'), 'CLAUDE_CONFIG_DIR': str(home),
            'DISABLE_AUTOUPDATER': '1', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'DISABLE_TELEMETRY': '1', 'DISABLE_ERROR_REPORTING': '1',
            'ANTHROPIC_API_KEY': 'sk-ant-offline-disposable-fixture',
            'ANTHROPIC_BASE_URL': 'http://127.0.0.1:9', 'CLAUDE_CODE_RESUME_INTERRUPTED_TURN': '0'}
        variables.update(extra_env or {})
        try:
            self.process = subprocess.Popen(command or [program, '--resume=' + str(resume), '--setting-sources=',
                '--settings', '{}', '--strict-mcp-config', '--disable-slash-commands', '--tools=',
                '--permission-mode', 'dontAsk'], stdin=slave, stdout=slave, stderr=slave,
                cwd=base / 'workspace', env=variables, start_new_session=True)
        except BaseException:
            os.close(self.master)
            raise
        finally:
            os.close(slave)

    def pump(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and self.process.poll() is None:
            if not select.select([self.master], [], [], .05)[0]:
                continue
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                break
            self.output.extend(chunk)
            if b'\x1b[6n' in chunk:
                os.write(self.master, b'\x1b[1;1R')
            if not self.approved and b'ANTHROPIC_API_KEY' in self.output and b'recommended' in self.output:
                # Confirm only the fixed, offline fixture key in this empty home.
                os.write(self.master, b'\x1b[A\r')
                self.approved = True

    def __enter__(self):
        deadline = time.monotonic() + 8
        try:
            while b'SOURCE_HISTORY_SENTINEL' not in self.output:
                if time.monotonic() > deadline or self.process.poll() is not None:
                    raise AssertionError('Native history did not open; inspect ' + str(self.base / (self.label + '.terminal')))
                self.pump(.1)
            self.pump(.3)
            return self
        except BaseException:
            self.__exit__()
            raise

    def session_id(self):
        record = self.home / 'sessions' / (str(self.process.pid) + '.json')
        if self.wrapped:
            records = list((self.home / 'sessions').glob('*.json'))
            if len(records) != 1: raise AssertionError('Expected one native child session marker')
            record = records[0]
        return json.loads(record.read_text())['sessionId']

    def rename(self, transcript, marker):
        os.write(self.master, ('/rename ' + marker + '\r').encode())
        deadline = time.monotonic() + 3
        while marker not in transcript.read_text():
            if time.monotonic() > deadline:
                raise AssertionError('Native rename did not write back to the original transcript')
            self.pump(.1)

    def __exit__(self, *_):
        (self.base / (self.label + '.terminal')).write_bytes(self.output)
        try:
            if self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=3)
        finally:
            os.close(self.master)


def main():
    program = shutil.which('claude')
    if program is None:
        raise SystemExit('Install Claude Code 2.1.316 before running this probe.')
    program = str(Path(program).resolve())
    base = Path(tempfile.mkdtemp(prefix='agent-claude-resume-check-'))
    print('Disposable fixture and terminal logs: ' + str(base), flush=True)
    for name in ('user', 'tmp', 'workspace', 'source', 'target'):
        (base / name).mkdir()
    variables = {'PATH': os.environ['PATH'], 'HOME': str(base / 'user'),
        'CLAUDE_CONFIG_DIR': str(base / 'source'), 'DISABLE_AUTOUPDATER': '1',
        'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1'}
    version = subprocess.run([program, '--version'], env=variables, cwd=base / 'workspace',
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5, check=True)
    if not version.stdout.startswith('2.1.316 '):
        raise SystemExit('This observation is pinned to Claude Code 2.1.316; review a new native version separately.')
    for home in (base / 'source', base / 'target'):
        (home / '.claude.json').write_text(json.dumps({'hasCompletedOnboarding': True, 'theme': 'dark',
            'projects': {str(base / 'workspace'): {'hasTrustDialogAccepted': True}}}))
    identifier, first = str(uuid.uuid4()), str(uuid.uuid4())
    directory = base / 'source/projects' / re.sub('[^a-zA-Z0-9]', '-', str(base / 'workspace'))
    directory.mkdir(parents=True)
    transcript = directory / (identifier + '.jsonl')
    envelope = {'sessionId': identifier, 'cwd': str(base / 'workspace'), 'isSidechain': False,
        'version': '2.1.316', 'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}
    rows = [dict(envelope, type='user', uuid=first, parentUuid=None,
                message={'role': 'user', 'content': 'SOURCE_HISTORY_SENTINEL'}),
            dict(envelope, type='assistant', uuid=str(uuid.uuid4()), parentUuid=first,
                message={'id': 'msg_fixture', 'type': 'message', 'role': 'assistant', 'model': 'fixture',
                    'content': [{'type': 'text', 'text': 'COMPLETED_SOURCE_REPLY'}],
                    'stop_reason': 'end_turn', 'stop_sequence': None,
                    'usage': {'input_tokens': 1, 'output_tokens': 1}})]
    transcript.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    with NativeTerminal(base, program, 'target-path-resume', base / 'target', transcript) as target:
        assert target.session_id() == identifier
        target.rename(transcript, 'CROSS_PROFILE_RENAME')
    assert not list((base / 'target/projects').glob('*/*.jsonl'))
    print('Observed: another home resumed the same ID and renamed the original transcript.', flush=True)
    with NativeTerminal(base, program, 'source-first', base / 'source', identifier) as first:
        with NativeTerminal(base, program, 'source-second', base / 'source', identifier) as second:
            assert first.process.poll() is None and second.process.poll() is None
            assert first.session_id() == second.session_id() == identifier
            first.rename(transcript, 'FIRST_CONCURRENT_RENAME')
            second.rename(transcript, 'SECOND_CONCURRENT_RENAME')
    print('Observed: two independent native processes opened the same ID and both appended metadata. '
          'Standalone processes bypass Manager coordination. No model turn or real login.', flush=True)


if __name__ == '__main__':
    main()
