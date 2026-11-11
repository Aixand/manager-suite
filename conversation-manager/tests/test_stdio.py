"""Real stdio subprocess checks without a provider account."""
import asyncio
import json
import sys
import unittest

from conversation_manager.native_codex import NativeCodexConnection, NativeCodexError
from conversation_manager.stdio import StdioTransport

PROVIDER = r'''
import json, sys
for raw in sys.stdin.buffer:
    request = json.loads(raw)
    if 'id' not in request:
        continue
    method = request.get('method')
    if method == 'disconnect':
        break
    if method == 'partial':
        sys.stdout.buffer.write(b'{"id":')
        sys.stdout.buffer.flush()
        break
    if method == 'approval':
        print(json.dumps({'id': 42, 'method': 'item/commandExecution/requestApproval', 'params': {'threadId': 'one'}}), flush=True)
    if method is None:
        print(json.dumps({'method': 'serverRequest/resolved', 'params': {'requestId': request['id']}}), flush=True)
        continue
    result = {'text': '界' * (2 * 1024 * 1024)} if method == 'large' else request.get('params', {})
    print(json.dumps({'id': request['id'], 'result': result}, ensure_ascii=False), flush=True)
'''


class StdioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.processes, self.clients = [], []

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        for process in self.processes:
            if process.returncode is None:
                process.kill()
            await process.communicate()

    async def client(self, *, maximum=10 * 1024 * 1024, events=None):
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-I', '-X', 'utf8', '-u', '-c', PROVIDER,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=maximum + 1)
        self.processes.append(process)
        transport = StdioTransport(process, max_message_bytes=maximum)
        client = NativeCodexConnection(None, transport=transport, max_message_bytes=maximum,
                                       timeout=5, on_event=None if events is None else events.append)
        self.clients.append(client)
        await client.connect()
        return client

    async def test_concurrent_unicode_messages_keep_their_request_identity(self):
        client = await self.client()
        values = [{'index': index, 'text': '界' * 4096} for index in range(20)]
        results = await asyncio.gather(*(client.request('echo', row) for row in values))
        self.assertEqual(results, values)

    async def test_saved_history_above_four_mib_is_preserved(self):
        client = await self.client()
        self.assertEqual(len((await client.request('large'))['text']), 2 * 1024 * 1024)
        self.assertEqual(await client.request('echo', {'still': 'ready'}), {'still': 'ready'})

    async def test_oversized_reply_marks_the_request_uncertain(self):
        client = await self.client(maximum=1024)
        with self.assertRaisesRegex(NativeCodexError, 'message limit') as caught:
            await client.request('large')
        self.assertTrue(caught.exception.uncertain)
        self.assertFalse(client.connected)

    async def test_eof_does_not_replay_a_request(self):
        client = await self.client()
        with self.assertRaises(NativeCodexError) as caught:
            await client.request('disconnect')
        self.assertTrue(caught.exception.uncertain)
        self.assertFalse(client.connected)

    async def test_partial_frame_is_not_accepted_as_a_reply(self):
        client = await self.client()
        with self.assertLogs('conversation_manager.native_codex', level='ERROR'):
            with self.assertRaises(NativeCodexError) as caught:
                await client.request('partial')
        self.assertTrue(caught.exception.uncertain)

    async def test_approval_uses_the_original_native_request_id(self):
        events = []
        client = await self.client(events=events)
        await client.request('approval')
        token = client.pending_requests[0]['requestId']
        await client.respond(token, {'decision': 'decline'})
        await client.request('echo')
        self.assertEqual(client.pending_requests, [])
        self.assertTrue(any(row.get('method') == 'serverRequest/resolved' for row in events))

    async def test_close_cancels_an_idle_reader(self):
        client = await self.client()
        async with asyncio.timeout(1):
            await client.close()
        self.assertFalse(client.connected)
