"""Worker-owned local Codex process and public Agent Manager environment lease."""
import asyncio
from copy import deepcopy
import os
import subprocess
import sys

from .agents import AgentEnvironments
from .models import ConversationError


class CodexRuntime:
    def __init__(self):
        self.process = self.guard = None

    async def start(self, descriptor, cwd):
        agents = AgentEnvironments('codex', config=descriptor['config'], root=descriptor['root'])
        self.guard = agents.lease_codex(descriptor)
        try:
            lease = self.guard.__enter__()
        except Exception as exc:
            self.guard = None
            raise ConversationError('conflict', 'The Codex environment is unavailable or changed; prepare again') from exc
        if lease.descriptor != descriptor:
            # Aliases are display metadata; they may change between preparations.
            expected, observed = deepcopy(descriptor), deepcopy(lease.descriptor)
            expected['target'].pop('aliases', None)
            observed['target'].pop('aliases', None)
            if expected != observed:
                raise ConversationError('conflict', 'The selected Codex environment changed')
        from .native_codex import MAX_MESSAGE_BYTES
        from .stdio import StdioTransport
        command = [descriptor['executable'], 'app-server', '--listen', 'stdio://']
        from aixand_local_runtime import process_start
        identity = process_start(os.getpid())
        if identity is None:
            await self.close()
            raise ConversationError('unsupported', 'Native process ownership could not be verified')
        command = [sys.executable, '-I', '-B', '-m', 'conversation_manager.native_process',
                   '--owner', str(os.getpid()), '--owner-start', identity, '--', *command]
        try:
            with lease.subprocess_options() as options:
                self.process = await asyncio.create_subprocess_exec(
                    *command, cwd=cwd, env=lease.env,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, limit=MAX_MESSAGE_BYTES + 1,
                    **options, **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
            return StdioTransport(self.process, max_message_bytes=MAX_MESSAGE_BYTES)
        except BaseException:
            await self.close()
            raise

    async def close(self):
        if self.process is not None:
            self.process.stdin.close()
            # Drain output so a full pipe cannot hold process.wait() open.
            async def drain():
                try:
                    while await self.process.stdout.read(65536):
                        pass
                except (OSError, ConnectionError):
                    pass
            output = asyncio.create_task(drain())
            try:
                # Let the owner finish job cleanup after the provider sees EOF.
                await asyncio.wait_for(self.process.wait(), 5)
            except asyncio.TimeoutError:
                try:
                    self.process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(self.process.wait(), 5)
                except asyncio.TimeoutError:
                    try:
                        self.process.kill()
                    except ProcessLookupError:
                        pass
            await self.process.wait()
            await output
            self.process = None
        if self.guard is not None:
            self.guard.__exit__(None, None, None)
            self.guard = None
