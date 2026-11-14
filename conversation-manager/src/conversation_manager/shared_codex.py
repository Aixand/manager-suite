"""Retained Codex server pool; conversation workers keep independent connections.

The pool is local to a Conversation Manager store, environment revision and cwd.
No credentials are persisted here. The owner holds Agent Manager's lease and
uses the existing native gateway for approval ownership and safe draining.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from aixand_filesystem import lock_file, private_directory, is_private
from aixand_local_runtime import process_start, install_signals

from .models import ConversationError, digest, canonical
from .native_gateway import NativeGateway, write_state


def pool_directory(home, descriptor, cwd):
    target = deepcopy(descriptor)
    target.get('target', {}).pop('aliases', None)
    key = digest([str(Path(home).resolve()), target, str(Path(cwd).resolve())])[7:31]
    from .launcher import _socket_root
    root = _socket_root() / f'cm-codex-pool-{os.getuid() if hasattr(os, "getuid") else "local"}'
    private_directory(root)
    if not is_private(root.stat()):
        raise ConversationError("validation", "Codex pool socket root must be private and owned by the current user")
    folder = root / key
    private_directory(folder)
    if not is_private(folder.stat()):
        raise ConversationError("validation", "Codex pool socket directory must be private and owned by the current user")
    return folder


@asynccontextmanager
async def pool_lock(folder, timeout=20):
    with (folder / 'owner.lock').open('a') as stream:
        os.chmod(stream.name, 0o600)
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            try:
                lock_file(stream, blocking=False)
                break
            except BlockingIOError:
                if asyncio.get_running_loop().time() >= deadline:
                    raise ConversationError('busy', 'Shared Codex server startup is busy') from None
                await asyncio.sleep(.05)
        yield


def owner_state(folder):
    try:
        return json.loads((folder / 'owner.json').read_text())
    except FileNotFoundError:
        return None


def live(state):
    return bool(state and state.get('start_ticks') is not None
                and process_start(state['pid']) == state['start_ticks'])


class SharedCodexRuntime:
    def __init__(self, home):
        self.home = home
        self.transport = None

    async def start(self, descriptor, cwd):
        if os.name == "nt":
            raise ConversationError("unsupported", "Shared Codex servers are not yet supported on Windows")
        # Revalidate even when reusing an owner; a retired/changed environment
        # must not gain new conversations through a stale pool entry.
        from .agents import AgentEnvironments
        agents = AgentEnvironments('codex', config=descriptor['config'], root=descriptor['root'])
        observed = await asyncio.to_thread(agents.prepare_native, descriptor['target']['environment_id'])
        expected, current = deepcopy(descriptor), deepcopy(observed)
        for value in (expected, current):
            value.get('target', {}).pop('aliases', None)
        if current != expected:
            raise ConversationError('conflict', 'The selected Codex environment changed')
        folder = pool_directory(self.home, descriptor, cwd)
        async with pool_lock(folder):
            state = owner_state(folder)
            deadline = asyncio.get_running_loop().time() + 15
            while live(state) and state.get("status") in ("draining", "stopped"):
                if asyncio.get_running_loop().time() >= deadline:
                    raise ConversationError("busy", "Shared Codex server is still stopping")
                await asyncio.sleep(.05)
                state = owner_state(folder)
            if not live(state):
                # A new epoch always gets fresh sockets and activity journals.
                epoch = uuid.uuid4().hex[:12]
                bootstrap = dict(folder=str(folder), descriptor=descriptor, cwd=cwd, epoch=epoch)
                process = subprocess.Popen(
                    [sys.executable, '-I', '-B', '-m', 'conversation_manager.shared_codex'],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    **({'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
                       if os.name == 'nt' else {'start_new_session': True}))
                import threading
                threading.Thread(target=process.wait, daemon=True).start()
                state = dict(pid=process.pid, start_ticks=process_start(process.pid),
                             epoch=epoch, status='starting')
                write_state(folder / 'owner.json', state)
                try:
                    process.stdin.write(canonical(bootstrap) + b'\n')
                    process.stdin.close()
                except (OSError, BrokenPipeError) as error:
                    raise ConversationError('uncertain', 'Shared Codex startup delivery is unconfirmed', uncertain=True) from error
            deadline = asyncio.get_running_loop().time() + 15
            while state.get('status') != 'ready':
                if not live(state) or state.get('status') == 'failed':
                    raise ConversationError('startup_failed', 'Shared Codex server failed to start')
                if state.get('status') in ('draining', 'stopped'):
                    raise ConversationError('busy', 'Shared Codex server is stopping; retry with a new startup operation')
                if asyncio.get_running_loop().time() >= deadline:
                    raise ConversationError('uncertain', 'Shared Codex server readiness is unconfirmed; no replacement was started', uncertain=True)
                await asyncio.sleep(.05)
                state = owner_state(folder)
            from aixand_local_runtime.websocket import connect
            from .native_codex import MAX_MESSAGE_BYTES
            # Hold admission lock until the gateway has accepted our connection.
            self.transport = await connect(state['endpoint'], compression=None,
                open_timeout=5, close_timeout=2, max_size=MAX_MESSAGE_BYTES, max_queue=4)
            return self.transport

    async def close(self):
        if self.transport is not None:
            await self.transport.close()
            self.transport = None


async def serve(options):
    from .codex_runtime import CodexRuntime
    folder, epoch = Path(options['folder']), options['epoch']
    runtime = CodexRuntime()
    gateway = None
    done = asyncio.Event()
    install_signals(done.set)
    state = dict(pid=os.getpid(), start_ticks=process_start(os.getpid()), epoch=epoch, status='starting')
    upstream, public, control = (folder / (epoch + suffix) for suffix in ('.up', '.ws', '.ctl'))
    try:
        await runtime.start(options['descriptor'], options['cwd'], endpoint=str(upstream))
        gateway = await NativeGateway(upstream, public, control, epoch).start()
        state.update(status='ready', endpoint=str(public))
        write_state(folder / 'owner.json', state)
        idle_since = time.monotonic()
        while not done.is_set():
            if runtime.process.returncode is not None:
                break
            if gateway.peers:
                idle_since = time.monotonic()
            elif time.monotonic() - idle_since >= 60:
                async with pool_lock(folder):
                    if not gateway.peers:
                        # No new client can enter while checking and sealing.
                        async with gateway.gate:
                            gateway.state['draining'] = 'pool-idle:' + epoch
                            gateway.save()
                            checked = await gateway.status()
                            if checked['ready']:
                                gateway.state['sealed'] = True
                                gateway.save()
                                state['status'] = 'draining'
                                write_state(folder / 'owner.json', state)
                                break
                            gateway.state['draining'] = None
                            gateway.save()
                idle_since = time.monotonic()
            try:
                await asyncio.wait_for(done.wait(), 1)
            except asyncio.TimeoutError:
                pass
    except Exception as error:
        state['status'] = 'failed'
        state['exception_type'] = type(error).__name__
        write_state(folder / 'owner.json', state)
    finally:
        if gateway:
            await gateway.close()
        await runtime.close()
        for path in (upstream, public, control, control.with_suffix('.json')):
            path.unlink(missing_ok=True)
        if state['status'] != 'failed':
            state['status'] = 'stopped'
            write_state(folder / 'owner.json', state)


def main():
    os.umask(0o077)
    from .transport import LIMIT
    raw = sys.stdin.buffer.readline(LIMIT)
    if not raw.endswith(b'\n'):
        raise ValueError('Invalid shared Codex bootstrap')
    asyncio.run(serve(json.loads(raw)))


if __name__ == '__main__':
    main()
