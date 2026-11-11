"""Private Codex RPC transport for native terminal continuation.

This connection verifies the history handoff; the native terminal handles user
interaction. Requests are correlated once and never replayed after failure.
"""
import asyncio
import json
import uuid

from agent_manager._version import __version__
from agent_manager.errors import Error


class CodexWireError(Error):
    def __init__(self, message, *, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


class NativeCodexConnection:
    def __init__(self, socket_path, *, timeout=30, capabilities=None, client_info=None):
        self.path, self.timeout = socket_path, timeout
        self.capabilities = capabilities or {}
        self.client_info = client_info or dict(name='agent_manager', title='Agent Manager', version=__version__)
        self.socket, self.reader = None, None
        self.pending = {}

    async def __aenter__(self):
        from aixand_local_runtime.websocket import connect
        deadline = asyncio.get_running_loop().time() + self.timeout
        try:
            while True:
                try:
                    self.socket = await connect(self.path, compression=None,
                        open_timeout=min(3, self.timeout), close_timeout=2,
                        max_size=64 * 1024 * 1024, max_queue=4)
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    if asyncio.get_running_loop().time() >= deadline:
                        raise CodexWireError('Native Codex socket did not become available') from None
                    await asyncio.sleep(.1)
            self.reader = asyncio.create_task(self._read())
            await self.request('initialize', dict(clientInfo=self.client_info, capabilities=self.capabilities))
            await self.socket.send(json.dumps(dict(method='initialized')))
            return self
        except BaseException:
            await self.close()
            raise

    async def __aexit__(self, *_): await self.close()

    async def _read(self):
        try:
            async for raw in self.socket:
                message = json.loads(raw)
                if not isinstance(message, dict): raise ValueError('Invalid RPC response')
                if 'method' in message:
                    # This verification connection cannot answer interactive
                    # approvals. Never silently approve a server request.
                    if 'id' in message:
                        await self.socket.send(json.dumps(dict(id=message['id'], error=dict(
                            code=-32601, message='Interactive requests require the native terminal'))))
                    continue
                future = self.pending.get(message.get('id'))
                if future is None or future.done(): continue
                if 'error' in message:
                    future.set_exception(CodexWireError('Native Codex rejected a continuation verification request'))
                else: future.set_result(message.get('result'))
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(CodexWireError(
                        'Native Codex disconnected; request outcomes are unconfirmed and were not replayed', uncertain=True))

    async def request(self, method, params=None):
        if self.reader is None or self.reader.done():
            raise CodexWireError('Native Codex is disconnected')
        if len(self.pending) >= 32: raise CodexWireError('Native Codex connection is busy')
        identifier = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[identifier] = future
        try:
            async with asyncio.timeout(self.timeout):
                await self.socket.send(json.dumps(dict(id=identifier, method=method, params=params or {})))
                return await future
        except CodexWireError:
            raise
        except (TimeoutError, OSError):
            raise CodexWireError('Native Codex request outcome is unconfirmed and was not replayed', uncertain=True) from None
        except asyncio.CancelledError:
            raise
        except Exception:
            raise CodexWireError('Native Codex transport failed; the request was not replayed', uncertain=True) from None
        finally:
            self.pending.pop(identifier, None)
            if not future.done(): future.cancel()

    async def close(self):
        try:
            if self.socket is not None: await self.socket.close()
        finally:
            if self.reader is not None:
                self.reader.cancel()
                await asyncio.gather(self.reader, return_exceptions=True)
            self.socket, self.reader = None, None
