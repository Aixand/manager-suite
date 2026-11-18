"""Disposable example infrastructure using real Undercurrent and Carrier SDKs.

Use a fresh short --work path, e.g. /tmp/prompt-worker-demo. No normal manager,
Carrier or Undercurrent stores are used. Evidence remains after owned processes stop.
"""
import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys

import httpx
from carrier import Client as Inbox, ensure_session
from undercurrent import Client as Runtime


def parser(description):
    cli = argparse.ArgumentParser(description=description)
    cli.add_argument('--work', type=Path, required=True, help='Fresh short directory for isolated stores and evidence')
    cli.add_argument('--runtime-bin', type=Path, required=True, help='Matching uc-daemon and uc-python-bridge directory')
    return cli


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.new')
    with temporary.open('w', encoding='utf-8') as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def show(label, **values):
    print(label + ': ' + json.dumps(values, sort_keys=True, allow_nan=False), flush=True)


async def until(read, predicate, *, timeout=15):
    async with asyncio.timeout(timeout):
        while True:
            result = await read()
            if predicate(result):
                return result
            await asyncio.sleep(.05)


class Demo:
    def __init__(self, root, runtime):
        self.root, self.runtime = root, runtime
        self.url = None
        self.connected = False
        self.database = root / 'carrier.db'
        self.credentials, self.addresses, self.sessions = {}, {}, []

    def identity(self, role):
        if not re.fullmatch('[a-z][a-z0-9-]*', role):
            raise ValueError('Use a simple role name')
        path = self.root / 'credentials' / (role + '.json')
        address = 'demo/' + role
        ensure_session(database=self.database, credentials=path, address=address)
        self.credentials[role], self.addresses[role] = path, address
        return path

    def client(self, role):
        if role not in self.credentials:
            self.identity(role)
        return Inbox(url=self.url, credentials=self.credentials[role])

    async def start(self, command, *, name, **options):
        session = await self.runtime.start([str(part) for part in command],
            name=name, cwd=str(self.root), detection=False, **options)
        self.sessions.append(session)
        save_json(self.root / 'processes.json', [{'id': row.id} for row in self.sessions])
        return session

    async def terminal(self, session, *, timeout=20):
        return await self.runtime.wait(session, lifecycle={'exited', 'failed', 'stopped'}, timeout=timeout)

    async def stop(self, session):
        observed = await self.runtime.inspect(session)
        if observed.lifecycle not in {'exited', 'failed', 'stopped'}:
            await self.runtime.stop(session, graceful_only=True)
        return await self.terminal(session)

    async def start_carrier(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        self.url = f'http://127.0.0.1:{port}'
        self.carrier = await self.start([sys.executable, '-B', '-m', 'carrier', 'serve',
            '--db', self.database, '--port', str(port)], name='carrier')
        async with httpx.AsyncClient(trust_env=False, timeout=.5) as health:
            async def ready():
                try:
                    response = await health.get(self.url + '/health')
                    return response.status_code == 200 and response.json() == {'status': 'ok', 'service': 'carrier'}
                except (httpx.HTTPError, ValueError):
                    return False
            await until(ready, bool)
        async with self.client('coordinator') as inbox:
            assert (await inbox.whoami())['address'] == 'demo/coordinator'


@asynccontextmanager
async def demo(args):
    root = args.work.expanduser().resolve()
    binaries = args.runtime_bin.expanduser().resolve()
    for name in ['uc-daemon', 'uc-python-bridge']:
        if not (binaries / name).is_file():
            raise ValueError('Missing runtime executable: ' + name)
    if len(os.fsencode(root / 'runtime/run/control.sock')) > 100:
        raise ValueError('Choose a shorter --work path such as /tmp/manager-demo')
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    runtime_home = root / 'runtime'
    runtime_home.mkdir(mode=0o700)
    daemon_log = (root / 'daemon.log').open('wb')
    daemon = subprocess.Popen([str(binaries / 'uc-daemon')],
        env=dict(os.environ, UNDERCURRENT_HOME=str(runtime_home)),
        stdin=subprocess.DEVNULL, stdout=daemon_log, stderr=daemon_log)
    runtime = Runtime(home=runtime_home, bridge=str(binaries / 'uc-python-bridge'), request_timeout=30)
    context = Demo(root, runtime)
    save_json(root / 'run.json', {'runtime_home': str(runtime_home), 'runtime_bin': str(binaries),
                                 'scope': 'isolated example; no provider account used by infrastructure'})
    try:
        async def daemon_ready():
            if daemon.poll() is not None:
                raise RuntimeError('Daemon exited; inspect daemon.log')
            return (runtime_home / 'run/control.sock').exists()
        await until(daemon_ready, bool)
        await runtime.connect()
        context.connected = True
        for role in ['coordinator', 'worker', 'reviewer', 'events']:
            context.identity(role)
        await context.start_carrier()
        yield context
    finally:
        failures = []
        # Other SDKs may launch nested work into this deliberately isolated runtime.
        # The entire runtime store belongs to this example, never the normal daemon.
        sessions = list(context.sessions)
        if context.connected:
            try:
                sessions = await runtime.list()
            except Exception as error:
                failures.append({'inventory_error': type(error).__name__})
        for session in reversed(sessions):
            try:
                await context.stop(session)
            except Exception as error:
                failures.append({'session_id': session.id, 'error_type': type(error).__name__})
        try:
            await runtime.close()
        finally:
            if daemon.poll() is None:
                daemon.terminate()
                try:
                    await asyncio.to_thread(daemon.wait, 10)
                except subprocess.TimeoutExpired:
                    daemon.kill()
                    await asyncio.to_thread(daemon.wait, 5)
                    failures.append({'daemon': 'required force after timeout'})
            daemon_log.close()
        save_json(root / 'cleanup.json', {'owned_sessions_stopped': not failures, 'failures': failures,
                                         'daemon_exited': daemon.poll() is not None})
        if failures:
            raise RuntimeError('Cleanup needs review; inspect cleanup.json and the isolated runtime store')
