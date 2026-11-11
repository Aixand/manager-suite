"""Native continuation uses its own local socket transport."""
import asyncio
import builtins
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aixand_local_runtime.websocket import serve
from agent_manager.runtime.native.codex_wire import NativeCodexConnection, CodexWireError


class WireTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='am-wire-')
        self.addCleanup(temporary.cleanup)
        self.path = str(Path(temporary.name) / 'rpc.sock')
        self.received = []
        self.behavior = 'normal'
        self.server = await serve(self.peer, self.path)

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def peer(self, socket):
        try:
            async for raw in socket:
                row = json.loads(raw)
                self.received.append(row)
                if 'method' not in row or 'id' not in row: continue
                method = row['method']
                if method != 'initialize':
                    if self.behavior == 'timeout': continue
                    if self.behavior == 'disconnect':
                        await socket.close()
                        return
                    if self.behavior == 'approval':
                        await socket.send(json.dumps(dict(id='approval', method='item/approval', params={})))
                    if self.behavior == 'reject':
                        await socket.send(json.dumps(dict(id=row['id'], error=dict(message='secret fixture data'))))
                        continue
                await socket.send(json.dumps(dict(method='notification', params={})))
                await socket.send(json.dumps(dict(id=row['id'], result=dict(method=method, params=row['params']))))
        except Exception:
            # Test clients deliberately close or time out mid-exchange.
            pass

    async def test_initialization_and_correlated_requests_without_server_manager(self):
        original = builtins.__import__
        def no_server(name, *args, **kwargs):
            if name == 'server_manager' or name.startswith('server_manager.'):
                raise AssertionError('Native CLI imported Server Manager')
            return original(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=no_server):
            async with NativeCodexConnection(self.path) as wire:
                results = await asyncio.gather(wire.request('config/read'), wire.request('thread/resume', {'threadId': 'saved'}))
        self.assertEqual([r['method'] for r in results], ['config/read', 'thread/resume'])
        self.assertEqual(results[1]['params'], {'threadId': 'saved'})
        self.assertEqual(self.received[0]['method'], 'initialize')
        self.assertEqual(self.received[1]['method'], 'initialized')
        self.assertFalse(wire.pending)

    async def test_rejected_request_does_not_expose_provider_payload(self):
        async with NativeCodexConnection(self.path) as wire:
            self.behavior = 'reject'
            with self.assertRaises(CodexWireError) as caught: await wire.request('thread/resume')
            self.assertFalse(caught.exception.uncertain)
            self.assertNotIn('secret', str(caught.exception))

    async def test_timeout_and_disconnect_never_replay(self):
        for behavior in ('timeout', 'disconnect'):
            async with NativeCodexConnection(self.path, timeout=.2) as wire:
                self.behavior = behavior
                with self.assertRaises(CodexWireError) as caught: await wire.request('thread/resume')
                self.assertTrue(caught.exception.uncertain)
                self.assertFalse(wire.pending)
            self.behavior = 'normal'
        self.assertEqual(sum(row.get('method') == 'thread/resume' for row in self.received), 2)

    async def test_server_approval_is_rejected_not_granted(self):
        async with NativeCodexConnection(self.path) as wire:
            self.behavior = 'approval'
            await wire.request('thread/resume')
            async with asyncio.timeout(2):
                while not any(row.get('id') == 'approval' for row in self.received): await asyncio.sleep(.01)
        response = next(row for row in self.received if row.get('id') == 'approval')
        self.assertIn('error', response)
        self.assertNotIn('result', response)


class TerminalDisconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_abrupt_disconnects_are_logged_without_terminal_tracebacks(self):
        from aixand_local_runtime.websocket import connect
        from agent_manager.runtime.native.codex_continue import terminal_endpoint
        for side in ('terminal', 'native'):
            with self.subTest(side=side), tempfile.TemporaryDirectory(prefix='am-proxy-') as folder:
                root = Path(folder)
                ready = asyncio.Event()
                finished = asyncio.Event()
                sockets = []

                async def native_peer(socket):
                    sockets.append(socket)
                    ready.set()
                    await finished.wait()

                server = await serve(native_peer, root / 'native.sock')
                log = root / 'diagnostics.log'
                try:
                    with self.assertNoLogs('websockets.server', level='ERROR'):
                        async with terminal_endpoint(root / 'native.sock', root / 'terminal.sock',
                                                     {'thread_id': 'saved', 'diagnostics': str(log)}):
                            async with connect(root / 'terminal.sock') as terminal:
                                await asyncio.wait_for(ready.wait(), timeout=2)
                                (terminal if side == 'terminal' else sockets[0]).transport.abort()
                                await asyncio.wait_for(terminal.wait_closed(), timeout=2)
                            # Exiting terminal_endpoint waits for all proxy handlers.
                    self.assertIn('connection closed', log.read_text())
                    self.assertIn('requests were not replayed', log.read_text())
                finally:
                    finished.set()
                    server.close()
                    await server.wait_closed()

    async def test_large_native_messages_survive_both_proxy_directions(self):
        from aixand_local_runtime.websocket import connect
        from agent_manager.runtime.native.codex_continue import terminal_endpoint
        # Resume replies can contain the complete conversation, exceeding the
        # old 16 MiB proxy limit. Exercise actual WebSocket framing both ways.
        payload = 'x' * (17 * 1024 * 1024)
        request = json.dumps({'id': 1, 'method': 'fixture/echo', 'params': {'data': payload}})
        response = json.dumps({'id': 1, 'result': {'data': payload}})
        received = []
        async def native_peer(socket):
            received.append(await socket.recv())
            await socket.send(response)
            await socket.wait_closed()
        with tempfile.TemporaryDirectory(prefix='am-proxy-size-') as folder:
            root = Path(folder)
            server = await serve(native_peer, root / 'native.sock', max_size=None, compression=None)
            try:
                async with terminal_endpoint(root / 'native.sock', root / 'terminal.sock', {'thread_id': 'saved'}):
                    async with connect(root / 'terminal.sock', max_size=None, compression=None) as terminal:
                        await terminal.send(request)
                        actual = await asyncio.wait_for(terminal.recv(), timeout=10)
                        self.assertEqual(actual, response)
                self.assertEqual(received, [request])
            finally:
                server.close()
                await server.wait_closed()
