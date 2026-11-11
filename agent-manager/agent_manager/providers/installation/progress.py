"""Measured installation stages and transfers, independent of any application UI."""
from pathlib import Path
import subprocess

from agent_manager.errors import Error


class InstallationProgress:
    def __init__(self, callback=None, method=None):
        self.callback = callback
        self.stages = ['preparing', *(['downloading'] if method == 'installer' else []), 'installing', 'verifying']
        self.known_method = method is not None
        self.previous = None

    def report(self, stage, *, transfer=None, existing=False):
        labels = {'preparing': 'Checking installation requirements', 'downloading': 'Downloading installer',
                  'installing': 'Running installer', 'verifying': 'Verifying installed version',
                  'complete': 'Existing installation verified' if existing else 'Installation verified'}
        completed = len(self.stages) if stage == 'complete' else self.stages.index(stage)
        value = dict(schema='agent-manager/install-progress-1', stage=stage, label=labels[stage],
                     steps=dict(completed=1 if existing else completed,
                                total=1 if existing else len(self.stages) if self.known_method else None))
        if transfer is not None:
            value['transfer'] = transfer
        if self.callback and value != self.previous:
            self.callback({'progress': value})
        self.previous = value


def transfer_size(headers):
    """Only the final successful response can supply the download's byte total."""
    try:
        data = headers.read_bytes()[-65536:].replace(b'\r\n', b'\n')
    except FileNotFoundError:
        return None
    # Ignore incomplete headers and interim/redirect responses.
    blocks = data.split(b'\n\n')[:-1]
    if not blocks:
        return None
    lines = blocks[-1].splitlines()
    if not lines or not lines[0].startswith(b'HTTP/'):
        return None
    status = lines[0].split()[1:2]
    if not status or not status[0].startswith(b'2'):
        return None
    fields = {key.strip().lower(): value.strip() for line in lines[1:] if b':' in line
              for key, value in [line.split(b':', 1)]}
    length = fields.get(b'content-length', b'')
    if b'transfer-encoding' in fields or not length.isdigit():
        return None
    total = int(length)
    return total if total > 0 else None


def download(args, script, reporter, *, cwd):
    """Keep curl's TLS, redirect and timeout rules; observe its actual file writes.

    stdout/stderr stay attached to the retained terminal. The percentage applies
    only to this installer script, never to the vendor's subsequent installation.
    """
    headers = Path(cwd) / 'download.headers'
    command = [*args[:-1], '--dump-header', str(headers), args[-1]]
    with subprocess.Popen(command, cwd=cwd) as process:
        try:
            while True:
                finished = process.poll() is not None
                try:
                    completed = script.stat().st_size
                except FileNotFoundError:
                    completed = 0
                total = transfer_size(headers)
                reporter.report('downloading', transfer=dict(completed=completed,
                    total=total if total is None or completed <= total else None, unit='bytes'))
                if finished:
                    break
                try:
                    process.wait(timeout=.2)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    if process.returncode:
        raise Error(f'Installer download failed (exit {process.returncode}); see the terminal output. Nothing was executed.')
