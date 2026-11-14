"""Integration checks using real retained workers and the offline Codex fixture."""
import asyncio
import json
import os
import signal
import unittest

import test_codex_local as local
from conversation_manager.shared_codex import pool_directory, owner_state, live


@unittest.skipUnless(local.Manager is not None and os.name != 'nt', 'Shared Codex qualification requires Agent Manager on POSIX')
class CodexSharingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await local.LocalCodexTests.asyncSetUp(self)
        if os.name == 'nt':
            return
        executable = self.root / 'codex'
        source = executable.read_text()
        source = source.replace('    tasks = set()', '    tasks = set()\n    decisions = {}')
        source = source.replace('        await asyncio.sleep(.2)', "        if text.startswith('approval:'):\n            request_id = uuid.uuid4().hex\n            future = asyncio.get_running_loop().create_future()\n            decisions[request_id] = future\n            await socket.send(json.dumps(dict(id=request_id,\n                method='item/commandExecution/requestApproval', params=dict(\n                    threadId=thread, turnId=identifier, itemId='cmd', command='echo fixture', cwd=str(home)))))\n            await future\n        await asyncio.sleep(.2)")
        source = source.replace("            method = request.get('method')", "            method = request.get('method')\n            if method is None and request.get('id') in decisions:\n                decisions.pop(request['id']).set_result(request.get('result'))\n                continue")
        executable.write_text(source)


    async def asyncTearDown(self):
        for connection in getattr(self, 'connections', []):
            await connection.disconnect()
        for index, ref in enumerate(getattr(self, 'refs', [])):
            await self.launcher.terminate(ref['runtime_ref'], operation_id=f'cleanup-{index}')
        for folder in getattr(self, 'pools', set()):
            state = owner_state(folder)
            if live(state):
                os.kill(state['pid'], signal.SIGTERM)
                async with asyncio.timeout(10):
                    while live(owner_state(folder)):
                        await asyncio.sleep(.05)
        self.tmp.cleanup()

    async def start(self, index, *, mode='shared', cwd=None, settings=None):
        self.connections = getattr(self, 'connections', [])
        self.refs = getattr(self, 'refs', [])
        self.pools = getattr(self, 'pools', set())
        cwd = cwd or self.root
        descriptor = self.agents.prepare_native(self.identifier)
        if mode == 'shared':
            self.pools.add(pool_directory(self.client.home, descriptor, cwd))
        launched = await self.client.launch_codex(self.identifier, cwd=cwd,
            agents=self.agents, operation_id=f'launch-{index}', server_mode=mode, settings=settings)
        ref = launched.get('result', {}).get('connection_ref') or launched.get('recovery', {}).get('connection_ref')
        if ref:
            self.refs.append(ref)
        self.assertEqual(launched['status'], 'completed', launched)
        connection = await self.client.connect(ref)
        self.connections.append(connection)
        return await connection.create_conversation(operation_id=f'create-{index}')

    async def complete(self, chat, text, index):
        sent = await chat.send(text, operation_id=f'send-{index}')
        async with asyncio.timeout(10):
            async for event in chat.events():
                if event['kind'] == 'turn.completed':
                    break
        return await chat.turn_result(sent['result']['turn_id'])

    async def test_five_workers_share_one_server_and_isolate_outputs_and_settings(self):
        chats = await asyncio.gather(*(self.start(i, settings={'model': f'model-{i}',
            'access': {'sandbox': 'read-only' if i % 2 == 0 else 'workspace-write', 'approvalPolicy': 'on-request'}}) for i in range(5)))
        launches = (self.native / 'launches.jsonl').read_text().splitlines()
        self.assertEqual(len(launches), 1)
        self.assertEqual(len({r['runtime_ref']['pid'] for r in self.refs}), 5)
        results = await asyncio.gather(*(self.complete(c, f'message-{i}', i) for i,c in enumerate(chats)))
        for i, result in enumerate(results):
            self.assertEqual(result['final_text'], f'fixture:message-{i}')
        threads = json.loads((self.native / 'fixture-threads.json').read_text())
        self.assertEqual({t['model'] for t in threads.values()}, {f'model-{i}' for i in range(5)})
        for thread in threads.values():
            index = int(thread['model'].split('-')[-1])
            self.assertEqual(thread['sandbox'], 'read-only' if index % 2 == 0 else 'workspace-write')

    async def test_stopping_one_worker_keeps_other_conversation_usable(self):
        first, second = await self.start(1), await self.start(2)
        await self.launcher.terminate(self.refs[0]['runtime_ref'], operation_id='stop-first')
        result = await self.complete(second, 'still alive', 2)
        self.assertEqual(result['final_text'], 'fixture:still alive')
        self.assertEqual(len((self.native / 'launches.jsonl').read_text().splitlines()), 1)

    async def test_workspace_and_separate_mode_do_not_share(self):
        other = self.root / 'other'; other.mkdir()
        await self.start(1)
        await self.start(2, cwd=other)
        await self.start(3, mode='separate')
        self.assertEqual(len((self.native / 'launches.jsonl').read_text().splitlines()), 3)

    async def test_crashed_pool_closes_workers_and_saved_thread_can_be_restored(self):
        first = await self.start(1)
        await self.complete(first, 'saved', 1)
        folder = next(iter(self.pools)); state = owner_state(folder)
        os.kill(state['pid'], signal.SIGKILL)
        async with asyncio.timeout(10):
            while not (await first.state())['closed'] or live(owner_state(folder)):
                await asyncio.sleep(.05)
        await self.launcher.terminate(self.refs[0]['runtime_ref'], operation_id='stop-disconnected')
        restored = await self.client.resume_conversation(first.reference, operation_id='restore')
        self.connections.append(restored.connection)
        self.refs.append(restored.reference['recovery']['connection_ref'])
        self.assertEqual(restored.reference['native_session_id'], first.reference['native_session_id'])
        self.assertEqual((await self.complete(restored, 'again', 2))['final_text'], 'fixture:again')
        self.assertEqual(len((self.native / 'launches.jsonl').read_text().splitlines()), 2)

    @unittest.skipIf(os.name == 'nt', 'Fixture approval injection uses a POSIX script')
    async def test_approvals_are_owned_by_the_requesting_conversation(self):
        first, second = await self.start(1), await self.start(2)
        sent = await first.send('approval:first', operation_id='request-approval')
        async with asyncio.timeout(10):
            while not (pending := await first.pending_requests()):
                await asyncio.sleep(.05)
        self.assertEqual(await second.pending_requests(), [])
        from conversation_manager import ConversationError
        with self.assertRaises(ConversationError):
            await second.respond(pending[0]['request_id'], {'native_result': {'decision': 'accept'}}, operation_id='wrong-owner')
        await first.respond(pending[0]['request_id'], {'native_result': {'decision': 'accept'}}, operation_id='approve')
        async with asyncio.timeout(10):
            async for event in first.events():
                if event['kind'] == 'turn.completed':
                    break
        self.assertEqual((await first.turn_result(sent['result']['turn_id']))['final_text'], 'fixture:approval:first')
        self.assertEqual((await self.complete(second, 'unaffected', 2))['final_text'], 'fixture:unaffected')

    async def test_launch_retries_keep_the_same_server_and_mode_is_part_of_intent(self):
        await self.start(1)
        repeat = await self.client.launch_codex(self.identifier, cwd=self.root, agents=self.agents,
            server_mode='shared', operation_id='launch-1')
        self.assertEqual(repeat['result']['connection_ref'], self.refs[0])
        from conversation_manager import ConversationError
        with self.assertRaises(ConversationError):
            await self.client.launch_codex(self.identifier, cwd=self.root, agents=self.agents,
                server_mode='separate', operation_id='launch-1')
        self.assertEqual(len((self.native / 'launches.jsonl').read_text().splitlines()), 1)
