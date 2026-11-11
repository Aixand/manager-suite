import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from conversation_manager.native_codex import NativeCodexConnection, NativeCodexError
from conversation_manager.native_gateway import NativeGateway, control


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from websockets.asyncio.server import unix_serve
        self.folder = tempfile.TemporaryDirectory(prefix="native-drain-")
        self.root = Path(self.folder.name)
        self.calls, self.wires, self.peers = [], [], []
        self.active, self.goal, self.queued, self.background = False, None, [], []
        self.native = await unix_serve(self.peer, str(self.root / "native"))
        self.gateway = await NativeGateway(self.root / "native", self.root / "public", self.root / "control", "epoch").start()

    async def asyncTearDown(self):
        for wire in self.wires:
            await wire.close()
        await self.gateway.close()
        self.native.close()
        await self.native.wait_closed()
        self.folder.cleanup()

    async def peer(self, socket):
        self.peers.append(socket)
        async for raw in socket:
            msg = json.loads(raw)
            self.calls.append(msg)
            method = msg.get("method")
            if not method and 'id' in msg:
                await socket.send(json.dumps({'method':'serverRequest/resolved','params':{'requestId':msg['id']}}))
            if not method or method == "initialized":
                continue
            result = {}
            if method == "remoteControl/status/read":
                result = {"status": "disabled"}
            elif method == "thread/loaded/list":
                result = {"data": ["saved-thread"], "nextCursor": None}
            elif method in ("thread/read", "thread/resume"):
                result = {"thread": {"id": "saved-thread", "status": {"type": "active" if self.active else "idle"}}}
            elif method == "thread/goal/get":
                result = {"goal": self.goal}
            elif method == 'account/login/start':
                result = {'type': 'chatgpt', 'loginId': 'pending-login', 'authUrl': 'private-login-url'}
            elif method == 'account/login/cancel':
                result = {'status': 'canceled'}
            elif method == "thread/queue/list":
                result = {"data": self.queued}
            elif method == "thread/backgroundTerminals/list":
                result = {"data": self.background}
            elif method == "turn/start":
                self.active = True
                result = {"turn": {"id": "turn", "status": "inProgress"}}
            elif method == "ask":
                await socket.send(json.dumps({"id": 7, "method": "item/commandExecution/requestApproval", "params": {"threadId": "saved-thread"}}))
            elif method == "lost":
                await socket.close()
                return
            elif method == "hold":
                continue
            await socket.send(json.dumps({"id": msg["id"], "result": result}))

    async def wire(self, **options):
        wire = NativeCodexConnection(str(self.root / "public"), timeout=1, **options)
        self.wires.append(wire)
        await wire.connect()
        return wire

    async def test_loaded_idle_conversations_do_not_hold_the_server_forever(self):
        await self.wire()
        result = await control(self.root / "control", "epoch", "begin_drain", operation_id="relogin")
        self.assertTrue(result["ready"])
        self.assertEqual(result["blockers"], [])

    async def test_browser_login_blocks_retirement_until_authoritative_completion(self):
        wire = await self.wire()
        await wire.request('account/login/start', {'type': 'chatgpt'})
        result = await control(self.root / 'control', 'epoch', 'begin_drain', operation_id='relogin')
        self.assertIn('logins', [row['kind'] for row in result['blockers']])
        self.assertFalse((await control(self.root / 'control', 'epoch', 'prepare_stop', operation_id='relogin'))['ready'])
        # An unrelated account notification or another login's completion does
        # not establish that this attempt finished.
        self.gateway.observe('test', {'method': 'account/updated', 'params': {}})
        self.gateway.observe('test', {'method': 'account/login/completed', 'params': {'loginId': 'other', 'success': True}})
        self.assertFalse((await control(self.root / 'control', 'epoch'))['ready'])
        self.gateway.observe('test', {'method': 'account/login/completed', 'params': {'loginId': 'pending-login', 'success': True}})
        self.assertTrue((await control(self.root / 'control', 'epoch'))['ready'])
        self.assertNotIn('private-login-url', self.gateway.path.read_text())

    async def test_login_cancel_remains_available_while_draining(self):
        wire = await self.wire()
        await wire.request('account/login/start', {'type': 'chatgpt'})
        await control(self.root / 'control', 'epoch', 'begin_drain', operation_id='relogin')
        await wire.request('account/login/cancel', {'loginId': 'pending-login'})
        self.assertTrue((await control(self.root / 'control', 'epoch'))['ready'])

    async def test_pending_login_survives_disconnect_and_controller_restart(self):
        wire = await self.wire()
        await wire.request('account/login/start', {'type': 'chatgpt'})
        await wire.close()
        await self.gateway.close()
        self.gateway = await NativeGateway(self.root / 'native', self.root / 'public', self.root / 'control', 'epoch').start()
        result = await control(self.root / 'control', 'epoch', 'begin_drain', operation_id='relogin')
        self.assertFalse(result['ready'])
        # A global notification reaches the observer after the original client
        # has gone, and settles the exact persisted native login ID.
        await self.gateway.observer._socket.send(json.dumps({'id': 999, 'method': 'hold'}))
        observer_socket = self.peers[-1]
        await observer_socket.send(json.dumps({'method': 'account/login/completed', 'params': {'loginId': 'pending-login', 'success': False}}))
        async with asyncio.timeout(2):
            while not (await control(self.root / 'control', 'epoch'))['ready']:
                await asyncio.sleep(.01)

    async def test_managed_home_rejects_direct_auth_mutation_before_dispatch(self):
        self.gateway.managed_auth = True
        wire = await self.wire()
        for method in ('account/login/start', 'account/logout', 'account/login/unknown'):
            with self.subTest(method=method), self.assertRaises(NativeCodexError) as caught:
                await wire.request(method, {'type': 'apiKey', 'apiKey': 'private-token'})
            self.assertEqual(caught.exception.code, 'managed_login_required')
            self.assertFalse(caught.exception.dispatched)
            self.assertNotIn(method, [row.get('method') for row in self.calls])
        self.assertNotIn('private-token', self.gateway.path.read_text())

    async def test_older_controller_ready_reply_cannot_authorize_retirement(self):
        from conversation_manager.native_gateway import SCHEMA

        async def legacy(reader, writer):
            request = json.loads(await reader.readline())
            writer.write((json.dumps({'schema': SCHEMA, 'epoch': request['epoch'],
                          'result': {'ready': True, 'blockers': []}}) + '\n').encode())
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_unix_server(legacy, str(self.root / 'legacy'))
        try:
            result = await control(self.root / 'legacy', 'epoch', 'prepare_stop', operation_id='relogin')
            self.assertFalse(result['ready'])
            self.assertEqual(result['blockers'], [{'kind': 'login_activity_unavailable'}])
        finally:
            server.close()
            await server.wait_closed()

    async def test_upgraded_journal_cannot_invent_missing_login_history(self):
        await self.gateway.close()
        previous = json.loads(self.gateway.path.read_text())
        previous.pop('activity_version')
        self.gateway.path.write_text(json.dumps(previous))
        self.gateway = await NativeGateway(self.root / 'native', self.root / 'public', self.root / 'control', 'epoch').start()
        result = await control(self.root / 'control', 'epoch', 'begin_drain', operation_id='relogin')
        self.assertFalse(result['ready'])
        self.assertIn('login_activity_unavailable', [row['kind'] for row in result['blockers']])

    async def test_second_client_cannot_start_work_after_admission_closes(self):
        first, second = await self.wire(), await self.wire()
        await first.request("turn/start", {"threadId": "saved-thread"})
        result = await control(self.root / "control", "epoch", "begin_drain", operation_id="relogin")
        self.assertFalse(result["ready"])
        self.assertIn("active_thread", [row["kind"] for row in result["blockers"]])
        with self.assertRaises(NativeCodexError) as caught:
            await second.request("turn/start", {"threadId": "saved-thread"})
        self.assertEqual(caught.exception.code, "auth_recovery_pending")
        self.assertFalse(caught.exception.dispatched)
        self.assertEqual(sum(row.get("method") == "turn/start" for row in self.calls), 1)
        self.active = False
        self.assertTrue((await control(self.root / "control", "epoch"))["ready"])

    async def test_approvals_keep_their_original_connection_and_can_finish_during_drain(self):
        first, second = await self.wire(), await self.wire()
        await first.request("ask")
        await second.request("ask")
        one, two = first.pending_requests[0]["requestId"], second.pending_requests[0]["requestId"]
        self.assertNotEqual(one, two)
        result = await control(self.root / "control", "epoch", "begin_drain", operation_id="relogin")
        self.assertFalse(result["ready"])
        await first.respond(one, {"decision": "decline"})
        await second.respond(two, {"decision": "decline"})
        async with asyncio.timeout(2):
            while not (await control(self.root / "control", "epoch"))["ready"]:
                await asyncio.sleep(.01)
        responses = [row for row in self.calls if row.get("id") == 7 and "result" in row]
        self.assertEqual(len(responses), 2)

    async def test_native_terminal_connection_blocks_retirement_even_between_turns(self):
        wire = await self.wire(client_info={"name": "codex_cli_rs", "version": "test"})
        result = await control(self.root / "control", "epoch", "begin_drain", operation_id="relogin")
        self.assertIn("native_clients", [row["kind"] for row in result["blockers"]])
        await wire.close()
        async with asyncio.timeout(2):
            while not (await control(self.root / "control", "epoch"))["ready"]:
                await asyncio.sleep(.01)

    async def test_draining_allows_exact_rejoin_but_rejects_resume_overrides(self):
        wire = await self.wire()
        self.active = True
        await control(self.root / "control", "epoch", "begin_drain", operation_id="relogin")
        self.assertEqual((await wire.request('thread/resume',{'threadId':'saved-thread'}))['thread']['id'],'saved-thread')
        with self.assertRaises(NativeCodexError):
            await wire.request('thread/resume',{'threadId':'saved-thread','model':'override'})
        self.active = False
        result = await control(self.root/'control','epoch','prepare_stop',operation_id='relogin')
        self.assertTrue(result['ready'])
        with self.assertRaises(NativeCodexError):
            await wire.request('thread/resume',{'threadId':'saved-thread'})

    async def test_autonomous_or_background_work_blocks_automatic_retirement(self):
        self.goal, self.queued, self.background = {"status": "active"}, [{"id": "queued"}], [{"processId": "background"}]
        result = await control(self.root / "control", "epoch", "begin_drain", operation_id="relogin")
        self.assertEqual({row["kind"] for row in result["blockers"]}, {"active_goal", "queued_work", "background_commands"})

    async def test_lost_mutation_is_not_replayed_or_treated_as_idle(self):
        wire = await self.wire()
        with self.assertRaises(NativeCodexError):
            await wire.request("lost")
        result = await control(self.root / "control", "epoch", "begin_drain", operation_id="relogin")
        self.assertFalse(result["ready"])
        self.assertIn("uncertain_delivery", [row["kind"] for row in result["blockers"]])
        self.assertEqual(sum(row.get("method") == "lost" for row in self.calls), 1)
        restored = NativeGateway(self.root / "native", self.root / "other", self.root / "control", "epoch")
        self.assertTrue(restored.state["uncertain"])

    async def test_newer_login_can_take_over_draining_without_reopening_admission(self):
        await control(self.root / "control", "epoch", "begin_drain", operation_id="old")
        result = await control(self.root / "control", "epoch", "begin_drain", operation_id="new")
        self.assertEqual(result["draining"], "new")
        self.assertTrue(result["ready"])

    async def test_control_requires_the_original_process_epoch(self):
        with self.assertRaises(ValueError):
            await control(self.root / "control", "other", "begin_drain", operation_id="relogin")
        self.assertIsNone(self.gateway.state["draining"])

    async def test_numeric_and_string_rpc_ids_remain_distinct_on_the_same_connection(self):
        wire = await self.wire()
        for identifier in (1,'1'):
            await wire._socket.send(json.dumps({'id':identifier,'method':'hold','params':{}}))
        async with asyncio.timeout(2):
            while len(self.gateway.state['inflight']) != 2:
                await asyncio.sleep(.01)
        await self.peers[-1].send(json.dumps({'id':1,'result':{}}))
        async with asyncio.timeout(2):
            while len(self.gateway.state['inflight']) != 1:
                await asyncio.sleep(.01)
        self.assertFalse((await control(self.root/'control','epoch','begin_drain',operation_id='relogin'))['ready'])
        await self.peers[-1].send(json.dumps({'id':'1','result':{}}))
        async with asyncio.timeout(2):
            while not (await control(self.root/'control','epoch'))['ready']:
                await asyncio.sleep(.01)
