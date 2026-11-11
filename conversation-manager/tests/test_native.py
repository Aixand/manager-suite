from __future__ import annotations
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from conversation_manager import Client, PreparedLaunch, StandaloneLauncher, ConversationError


class NativeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from websockets.asyncio.server import unix_serve
        self.tmp = tempfile.TemporaryDirectory(prefix="server-native-")
        self.home = Path(self.tmp.name)
        self.calls, self.responses, self.cancel_events, self.tasks, self.sessions = [], {}, {}, set(), {}
        self.reply_messages = []
        self.mcp_rows = [{"name": "native-mcp", "authStatus": "unknown", "tools": {"fixture": {}}}]
        self.socket = str(self.home / "native.sock")
        self.server = await unix_serve(self.handler, self.socket, compression=None)
        self.client, self.launcher = Client(self.home), StandaloneLauncher(self.home)
        prepared = PreparedLaunch(backend_id="codex", adapter_id="native-codex", protocol="codex-app-server",
            argv=[], cwd=str(self.home), transport={"kind": "unix-websocket", "endpoint": self.socket})
        launched = await self.client.launch(prepared, launcher=self.launcher, operation_id="native-launch")
        self.assertEqual(launched["status"], "completed", launched)
        self.ref = launched["result"]["connection_ref"]
        self.connection = await self.client.connect(self.ref)

    async def asyncTearDown(self):
        await self.connection.disconnect()
        await self.launcher.terminate(self.ref["runtime_ref"], operation_id="native-stop")
        for task in self.tasks: task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.server.close(); await self.server.wait_closed()
        self.tmp.cleanup()

    async def handler(self, socket):
        async for raw in socket:
            message = json.loads(raw)
            if "method" not in message:
                self.reply_messages.append(message)
                future = self.responses.pop(message["id"], None)
                if future and not future.done(): future.set_result(message["result"])
                continue
            self.calls.append(message)
            method, params = message["method"], message.get("params", {})
            if method == "initialized": continue
            if method == "initialize":
                result = {"serverInfo": {"version": "0.162.1"}}
            elif method == "thread/start":
                identifier = str(uuid.uuid4())
                self.sessions[identifier] = {"id": identifier, **params}
                result = {"thread": self.sessions[identifier], "model": "native-fixture"}
            elif method == "thread/resume": result = {"thread": self.sessions[params["threadId"]]}
            elif method == "thread/read": result = {"thread": {**self.sessions[params["threadId"]], "turns": []}}
            elif method == 'model/list': result = {'data': [{'id': 'native-fixture', 'model': 'native-fixture'}]}
            elif method == 'thread/name/set':
                self.sessions[params['threadId']]['name'] = params['name']; result = {}
            elif method == 'account/rateLimits/read': result = {'rateLimits': {'primary': {'usedPercent': 25}}}
            elif method == 'thread/goal/set':
                result = {'goal': {'objective': params.get('objective'), 'status': params.get('status')}}
                if params.get('objective') == 'hold goal':
                    turn = str(uuid.uuid4())
                    self.cancel_events[params['threadId']] = asyncio.Event()
                    await socket.send(json.dumps({'method': 'turn/started', 'params': {'threadId': params['threadId'], 'turn': {'id': turn, 'status': 'inProgress'}}}))
                    self.tasks.add(asyncio.create_task(self.turn(socket, params['threadId'], turn, 'hold')))
            elif method in ('review/start', 'thread/compact/start'):
                turn = str(uuid.uuid4())
                result = {'turn': {'id': turn, 'status': 'inProgress'}, 'reviewThreadId': params['threadId']} if method == 'review/start' else {}
                self.cancel_events[params['threadId']] = asyncio.Event()
                await socket.send(json.dumps({'method': 'turn/started', 'params': {
                    'threadId': params['threadId'], 'turn': {'id': turn, 'status': 'inProgress'}}}))
                await socket.send(json.dumps({'id': message['id'], 'result': result}))
                text = params.get('target', {}).get('instructions', 'compact')
                self.tasks.add(asyncio.create_task(self.turn(socket, params['threadId'], turn, text)))
                continue
            elif method == "turn/start":
                turn = str(uuid.uuid4()); result = {"turn": {"id": turn, "status": "inProgress"}}
                if getattr(self, 'delay_start', False):
                    await asyncio.sleep(.1)
                await socket.send(json.dumps({"id": message["id"], "result": result}))
                text = "".join(b.get("text", '') for b in params["input"])
                self.cancel_events[params["threadId"]] = asyncio.Event()
                task = asyncio.create_task(self.turn(socket, params["threadId"], turn, text))
                self.tasks.add(task)
                continue
            elif method == 'turn/steer':
                result = {'turnId': params['expectedTurnId']}
            elif method == "turn/interrupt":
                self.cancel_events[params["threadId"]].set(); result = {}
            elif method == "mcpServerStatus/list":
                result = {"data": self.mcp_rows, "nextCursor": None}
            else:
                await socket.send(json.dumps({"id": message["id"], "error": {"code": -32601, "message": "unknown"}})); continue
            await socket.send(json.dumps({"id": message["id"], "result": result}))

    async def turn(self, socket, session, turn, text):
        if text in ("approval", "question", "permissions", "file-approval", "legacy-approval"):
            identifier = "native-request:" + uuid.uuid4().hex
            self.responses[identifier] = asyncio.get_running_loop().create_future()
            method = {"approval": "item/commandExecution/requestApproval", "question": "item/tool/requestUserInput",
                      "permissions": "item/permissions/requestApproval", "file-approval": "item/fileChange/requestApproval",
                      "legacy-approval": "execCommandApproval"}[text]
            await socket.send(json.dumps({"id": identifier, "method": method,
                "params": {"threadId": session, "turnId": turn, "itemId": "native-tool"}}))
            await self.responses[identifier]
        await socket.send(json.dumps({"method": "item/agentMessage/delta", "params": {
            "threadId": session, "turnId": turn, "itemId": "native-message", "delta": text}}))
        await socket.send(json.dumps({"method": "item/started", "params": {"threadId": session,
            "turnId": turn, "item": {"id": "native-tool", "type": "commandExecution", "status": "inProgress"}}}))
        if text == "hold": await self.cancel_events[session].wait()
        else: await asyncio.sleep(.05)
        await socket.send(json.dumps({"method": "item/completed", "params": {"threadId": session,
            "turnId": turn, "item": {"id": "native-tool", "type": "commandExecution", "status": "completed"}}}))
        status = "interrupted" if self.cancel_events[session].is_set() else "completed"
        await socket.send(json.dumps({"method": "turn/completed", "params": {"threadId": session,
            "turn": {"id": turn, "status": status}}}))

    async def session(self, settings=None):
        result = await self.connection.create_session(settings or {}, operation_id=uuid.uuid4().hex)
        self.assertEqual(result["status"], "completed", result)
        return result["result"]["session_ref"]

    async def test_frontend_slash_catalog_distinguishes_commands_settings_and_terminal_entries(self):
        session = await self.session()
        conversation = await self.connection.attach_conversation(session)
        listing = await conversation.list_slash_commands()
        rows = {row['name']: row for row in listing['commands']}
        self.assertTrue(rows['review']['executable'])
        self.assertEqual(rows['review']['slash'], '/review')
        self.assertEqual(rows['model']['execution'], 'setting')
        self.assertFalse(rows['model']['executable'])
        self.assertFalse(rows['hooks']['available'])
        self.assertTrue(rows['hooks']['terminal_required'])
        supported = await conversation.list_slash_commands(available_only=True)
        self.assertTrue(all(row['available'] for row in supported['commands']))
        self.assertNotIn('turn/start', [call['method'] for call in self.calls])

    async def done(self, key):
        async with asyncio.timeout(5):
            while True:
                row = await self.client.operation(key)
                if row["status"] != "accepted": return row
                await asyncio.sleep(.02)

    async def test_native_initialization_thread_payload_and_saved_instructions_resume(self):
        settings = {"instructions": {"text": "saved instructions", "digest": "saved", "snapshot_ref": {"id": "saved"}},
            "model": "fixture-model", "access": {"sandbox": "workspace-write", "approvalPolicy": "on-request"}}
        session = await self.session(settings)
        self.assertEqual(self.calls[0]["method"], "initialize")
        self.assertEqual(self.calls[1], {"method": "initialized"})
        start = next(r["params"] for r in self.calls if r["method"] == "thread/start")
        self.assertEqual(start, {"cwd": str(self.home), "model": "fixture-model", "developerInstructions": "saved instructions",
                                 "sandbox": "workspace-write", "approvalPolicy": "on-request"})
        loaded = await self.connection.load_session(session, operation_id="native-load")
        self.assertEqual(loaded["result"]["session_ref"], session)
        resume = next(r["params"] for r in self.calls if r["method"] == "thread/resume")
        self.assertEqual(resume, {"threadId": session["native_session_id"], "developerInstructions": "saved instructions",
                                  "sandbox": "workspace-write", "approvalPolicy": "on-request"})

    async def test_captured_stdio_mcp_maps_on_create_and_resume(self):
        row = dict(server_name='agent_manager_captured', kind='stdio', command='/usr/bin/example',
            args=['--stdio'], cwd=str(self.home), env_vars=[], startup_timeout_sec=60, tool_timeout_sec=90)
        session = await self.session({'mcp': {'servers': [row]}})
        expected = {'mcp_servers.agent_manager_captured': {key:value for key,value in row.items()
            if key in ('command','args','cwd','startup_timeout_sec','tool_timeout_sec')}}
        start = next(call['params'] for call in self.calls if call['method']=='thread/start')
        self.assertEqual(start['config'], expected)
        await self.connection.load_session(session, operation_id='captured-resume')
        resume = next(call['params'] for call in self.calls if call['method']=='thread/resume')
        self.assertEqual(resume['config'], expected)
        target = await self.connection.mcp_target(session)
        connector = self.connection.mcp_connector()
        self.assertTrue((await connector.capabilities(target))['inventory'])
        self.assertEqual((await connector.inventory(target, scope='session'))['target'], target)
        with self.assertRaises(ConversationError):
            await connector.inventory({**target, 'selection_digest':'changed'}, scope='session')
        bad = await self.connection.create_session({'mcp': {'servers': [dict(row, server_name='unscoped')] }}, operation_id='unscoped')
        self.assertEqual(bad['status'], 'failed', bad)
        conflict = await self.connection.create_session({'mcp': {'servers':[row], 'native_config':{'mcp_servers.agent_manager_other':{}}}}, operation_id='conflicting-native')
        self.assertEqual(conflict['status'], 'failed', conflict)
        self.assertEqual(len([call for call in self.calls if call['method']=='thread/start']), 1)

    async def test_native_turn_streaming_identity_and_history(self):
        session = await self.session()
        await self.connection.send(session, "hello", operation_id="native-send")
        result = await self.done("native-send")
        artifact = await self.connection.turn_result(session, result['result']['turn_id'])
        self.assertIsInstance(artifact['final_text'], str)
        self.assertTrue(artifact['final_text'])
        self.assertEqual(result["status"], "completed")
        self.assertIsNotNone(result["result"]["native_turn_id"])
        sent = next(r["params"] for r in self.calls if r["method"] == "turn/start")
        self.assertEqual(sent, {"threadId": session["native_session_id"], "input": [{"type": "text", "text": "hello"}]})
        events = (await self.connection.event_page(session))["events"]
        self.assertEqual([e["kind"] for e in events], ["message.delta", "tool.start", "tool.result", "turn.completed"])
        self.assertEqual(events[-1]["native_turn_id"], result["result"]["native_turn_id"])
        history = await self.connection.history(session)
        self.assertEqual(history["thread"]["id"], session["native_session_id"])
        self.assertEqual(self.calls[-1]["params"], {"threadId": session["native_session_id"], "includeTurns": True})

    async def test_restore_on_replacement_wrapper_preserves_thread_and_never_sends(self):
        settings = {'model': 'fixture-model', 'access': {'sandbox': 'read-only', 'approvalPolicy': 'on-request'}}
        saved = await self.session(settings)
        captured = self.client.store.get('session', saved['session_id'])['settings']
        retained = self.client.store.get('session', saved['session_id'])
        confirmed = {'model': 'confirmed-model', 'serviceTier': 'fast', 'effort': 'high', 'approvalPolicy': 'never',
            'permissions': ':read-only', 'collaborationMode': {'mode': 'plan', 'settings': {'model': 'confirmed-model', 'reasoning_effort': 'high', 'developer_instructions': None}}}
        self.client.store.put('session', saved['session_id'], {**retained, 'native_settings': confirmed})
        await self.connection.disconnect()
        await self.launcher.terminate(self.ref['runtime_ref'], operation_id='restore-retire')
        prepared = PreparedLaunch(backend_id='codex', adapter_id='native-codex', protocol='codex-app-server',
            argv=[], cwd=str(self.home), transport={'kind': 'unix-websocket', 'endpoint': self.socket}, settings=captured)
        launched = await self.client.launch(prepared, launcher=self.launcher, operation_id='restore-launch')
        replacement = await self.client.connect(launched['result']['connection_ref'])
        try:
            result = await replacement.restore_session(saved, launcher=self.launcher, operation_id='restore-thread')
            self.assertEqual(result['status'], 'completed', result)
            restored = result['result']['session_ref']
            self.assertEqual(restored['native_session_id'], saved['native_session_id'])
            self.assertNotEqual(restored['connection_id'], saved['connection_id'])
            self.assertEqual(self.client.store.get('session', restored['session_id'])['settings'], captured)
            self.assertEqual(self.client.store.get('session', restored['session_id'])['native_settings'], confirmed)
            resumed = next(row['params'] for row in self.calls if row['method'] == 'thread/resume')
            self.assertEqual(resumed['model'], 'confirmed-model')
            self.assertEqual(resumed['serviceTier'], 'fast')
            self.assertEqual(resumed['config']['model_reasoning_effort'], 'high')
            self.assertEqual(resumed['permissions'], ':read-only')
            self.assertNotIn('sandbox', resumed)
            self.assertEqual(await replacement.restore_session(saved, launcher=self.launcher, operation_id='restore-thread'), result)
            self.assertEqual(sum(row['method'] == 'thread/start' for row in self.calls), 1)
            self.assertFalse(any(row['method'] == 'turn/start' for row in self.calls))
        finally:
            await replacement.disconnect()
            await self.launcher.terminate(launched['result']['connection_ref']['runtime_ref'], operation_id='restore-stop')

    async def test_native_commands_track_turns_replay_and_cancel(self):
        session = await self.session()
        for command in ('compact', 'review'):
            key = 'command-' + command
            started = await self.connection.command(session, command, operation_id=key)
            self.assertEqual(started['status'], 'accepted', started)
            completed = await self.done(key)
            self.assertEqual(completed['status'], 'completed', completed)
            self.assertIsNotNone(completed['result']['native_turn_id'])
            self.assertEqual(await self.connection.command(session, command, operation_id=key), completed)
        self.assertEqual([row['method'] for row in self.calls if row['method'] in ('turn/start', 'review/start', 'thread/compact/start')],
                         ['thread/compact/start', 'review/start'])
        started = await self.connection.command(session, 'review', 'hold', operation_id='command-held')
        async with asyncio.timeout(5):
            while not any(row['kind'] == 'session.status' and row['turn_id'] == started['result']['turn_id']
                          for row in (await self.connection.event_page(session))['events']):
                await asyncio.sleep(.02)
        busy = await self.connection.send(session, 'cannot overlap', operation_id='command-busy')
        self.assertEqual(busy['error']['code'], 'busy')
        cancelled = await self.connection.cancel(session, started['result']['turn_id'], operation_id='command-cancel')
        self.assertIn(cancelled['status'], ('accepted', 'completed'))
        self.assertEqual((await self.done('command-held'))['result']['status'], 'cancelled')
        self.assertEqual((await self.done('command-cancel'))['status'], 'completed')

    async def test_direct_command_receipts_and_provider_goal_continuations(self):
        session = await self.session()
        catalog = await self.connection.commands(session)
        self.assertTrue({'rename', 'permissions', 'plan', 'mcp', 'usage', 'goal', 'plugins'} <= {row['name'] for row in catalog['commands']})
        renamed = await self.connection.command(session, 'rename', 'Release prep', operation_id='rename-direct')
        self.assertEqual(renamed['status'], 'completed', renamed)
        self.assertEqual(renamed['result']['title'], 'Release prep')
        await self.connection.command(session, 'rename', 'Release prep', operation_id='rename-direct')
        self.assertEqual(sum(row['method'] == 'thread/name/set' for row in self.calls), 1)
        usage = await self.connection.command(session, 'usage', operation_id='usage-direct')
        self.assertEqual(usage['result']['output']['rateLimits']['primary']['usedPercent'], 25)
        goal = await self.connection.command(session, 'goal', 'hold goal', operation_id='goal-direct')
        self.assertEqual(goal['status'], 'completed', goal)
        async with asyncio.timeout(5):
            while not (statuses := [event for event in (await self.connection.event_page(session))['events'] if event['kind'] == 'session.status']):
                await asyncio.sleep(.02)
        turn_id = statuses[-1]['payload']['turn_id']
        busy = await self.connection.send(session, 'cannot overlap', operation_id='goal-busy')
        self.assertEqual(busy['error']['code'], 'busy')
        # Inspection stays usable while the provider's continuation is running.
        self.assertEqual((await self.connection.command(session, 'usage', operation_id='usage-running'))['status'], 'completed')
        await self.connection.cancel(session, turn_id, operation_id='goal-cancel')
        self.assertEqual((await self.done('goal-cancel'))['status'], 'completed')
        self.assertFalse(any(row['method'] == 'turn/start' for row in self.calls))

    async def test_native_permissions_and_user_input_preserve_decisions(self):
        for text, answer, kind in (("approval", {"decision": "decline"}, "permission"), ("question", {"answers": {"q": {"answers": ["a"]}}}, "user_input")):
            session = await self.session(); key = "native-" + text
            await self.connection.send(session, text, operation_id=key)
            async with asyncio.timeout(5):
                while not (rows := await self.connection.pending_requests(session)): await asyncio.sleep(.02)
            self.assertEqual(rows[0]["kind"], kind)
            self.assertTrue(rows[0]["native_request_id"].startswith("native-request:"))
            bad = await self.connection.respond(rows[0]["request_id"], {"outcome": "cancelled"}, operation_id=key+"-bad")
            self.assertEqual(bad["error"]["code"], "validation")
            good = await self.connection.respond(rows[0]["request_id"], {"native_result": answer}, operation_id=key+"-reply")
            self.assertEqual(good["status"], "completed")
            self.assertEqual((await self.done(key))["status"], "completed")

    async def test_native_cancel_rpc_and_observed_completion(self):
        session = await self.session(); sent = await self.connection.send(session, "hold", operation_id="native-hold")
        async with asyncio.timeout(5):
            while not (await self.connection.event_page(session))["events"]: await asyncio.sleep(.02)
        cancel = await self.connection.cancel(session, sent["result"]["turn_id"], operation_id="native-cancel")
        self.assertIn(cancel["status"], ("accepted", "completed"))
        self.assertEqual((await self.done("native-hold"))["result"]["status"], "cancelled")
        self.assertTrue((await self.done("native-cancel"))["result"]["cancellation_observed"])
        interrupt = next(r["params"] for r in self.calls if r["method"] == "turn/interrupt")
        self.assertEqual(interrupt["threadId"], session["native_session_id"])
        self.assertTrue(interrupt["turnId"])

    async def test_steer_and_stop_can_wait_for_initial_native_acknowledgment(self):
        self.delay_start = True
        session = await self.session()
        sent = await self.connection.send(session, 'hold', operation_id='starting-turn')
        args = dict(expected_turn_id=sent['result']['turn_id'], operation_id='starting-steer')
        steered = await self.connection.steer(session, 'another input', **args)
        self.assertEqual(steered['status'], 'completed', steered)
        self.assertEqual(await self.connection.steer(session, 'another input', **args), steered)
        self.assertEqual(sum(row['method'] == 'turn/steer' for row in self.calls), 1)
        await self.connection.cancel(session, sent['result']['turn_id'], operation_id='starting-stop')
        self.assertEqual((await self.done('starting-turn'))['result']['status'], 'cancelled')
        second = await self.connection.send(session, 'hold', operation_id='early-stop-turn')
        stopped = await self.connection.cancel(session, second['result']['turn_id'], operation_id='early-stop')
        self.assertIn(stopped['status'], ('accepted', 'completed'))
        self.assertEqual((await self.done('early-stop-turn'))['result']['status'], 'cancelled')

    async def test_permissions_profiles_validate_by_method_and_reply_exactly_once(self):
        session = await self.session()
        answers = [{"permissions": {}, "scope": "turn"}, {"permissions": {}, "scope": "session"},
                   {"permissions": {}}, {"permissions": {"network": {"enabled": True},
                    "fileSystem": {"write": [str(self.home)], "entries": [{"access": "read",
                        "path": {"type": "path", "path": str(self.home)}}]}}, "scope": "session", "strictAutoReview": False}]
        for i, answer in enumerate(answers):
            key = "profile-" + str(i)
            await self.connection.send(session, "permissions", operation_id=key)
            async with asyncio.timeout(5):
                while not (rows := await self.connection.pending_requests(session)): await asyncio.sleep(.02)
            request = rows[0]
            self.assertEqual(request["native_method"], "item/permissions/requestApproval")
            self.assertEqual(request["response_schema"]["required"], ["permissions"])
            for j, invalid in enumerate(({"decision": "decline"}, {"permissions": [], "scope": "turn"},
                    {"permissions": {}, "scope": "forever"}, {"permissions": {"network": True}},
                    {"permissions": {}, "strictAutoReview": "yes"})):
                bad = await self.connection.respond(request["request_id"], {"native_result": invalid}, operation_id=key+"-bad-"+str(j))
                self.assertEqual(bad["error"]["code"], "validation")
                self.assertEqual(len(self.reply_messages), i)
            replies = await asyncio.gather(*(self.connection.respond(request["request_id"], {"native_result": answer},
                operation_id=key+"-reply-"+str(j)) for j in range(2)))
            self.assertEqual(sorted(row["status"] for row in replies), ["completed", "failed"])
            self.assertEqual(self.reply_messages[-1], {"id": request["native_request_id"], "result": answer})
            self.assertEqual(len(self.reply_messages), i+1)
            self.assertEqual((await self.done(key))["status"], "completed")

    async def test_native_decision_variants_are_not_coerced_or_shared_between_methods(self):
        answers = [("approval", {"decision": {"acceptWithExecpolicyAmendment": {"execpolicy_amendment": ["echo"]}}}),
                   ("approval", {"decision": {"applyNetworkPolicyAmendment": {"network_policy_amendment": {"action": "deny", "host": "example.test"}}}}),
                   ("file-approval", {"decision": "cancel"}), ("legacy-approval", {"decision": {"denied": {"rejection": "No"}}})]
        session = await self.session()
        for i, (text, answer) in enumerate(answers):
            key = "decision-"+str(i)
            await self.connection.send(session, text, operation_id=key)
            async with asyncio.timeout(5):
                while not (rows := await self.connection.pending_requests(session)): await asyncio.sleep(.02)
            if text == "file-approval":
                bad = await self.connection.respond(rows[0]["request_id"], {"native_result": answers[0][1]}, operation_id=key+"-bad")
                self.assertEqual(bad["error"]["code"], "validation")
            good = await self.connection.respond(rows[0]["request_id"], {"native_result": answer}, operation_id=key+"-reply")
            self.assertEqual(good["status"], "completed", good)
            self.assertEqual(self.reply_messages[-1]["result"], answer)
            repeated = await self.connection.respond(rows[0]["request_id"], {"native_result": answer}, operation_id=key+"-reply")
            self.assertEqual(repeated, good)
            stale = await self.connection.respond(rows[0]["request_id"], {"native_result": answer}, operation_id=key+"-stale")
            self.assertEqual(stale["error"]["code"], "conflict")
            self.assertEqual((await self.done(key))["status"], "completed")
        self.assertEqual(len(self.reply_messages), len(answers))

    async def test_native_mcp_runtime_status_normalizes_only_observed_evidence(self):
        session = await self.session()
        states = {"connected": "connected", "starting": "connecting", "failed": "failed",
                  "cancelled": "disconnected", "disabled": "disconnected", None: "unknown", "future": "unknown"}
        self.mcp_rows = [{"name": "server-"+str(i), "runtimeStatus": state, "tools": {}} for i, state in enumerate(states)]
        self.mcp_rows.append({"name": "unavailable", "connectionStatus": "connected", "tools": {"listed": {}}})
        target = {"schema": "mcp-manager/runtime-target-1", "connector_id": "conversation-manager", "backend_id": "codex",
                  "connection_ref": self.ref, "runtime_ref": self.ref["runtime_ref"], "native_session_id": session["native_session_id"]}
        inventory = await self.connection.mcp_connector().inventory(target, scope="session")
        self.assertEqual([row["connection_status"] for row in inventory["servers"]], list(states.values())+["unknown"])
        self.assertEqual([row["native"] for row in inventory["servers"]], self.mcp_rows)

    async def test_native_mcp_unknown_state_and_no_scope_widening(self):
        session = await self.session()
        target = {"schema": "mcp-manager/runtime-target-1", "connector_id": "conversation-manager", "backend_id": "codex",
            "connection_ref": self.ref, "runtime_ref": self.ref["runtime_ref"], "native_session_id": session["native_session_id"],
            "selection_ref": None, "selection_digest": None, "prepared_generations": []}
        mcp = self.connection.mcp_connector()
        capabilities = await mcp.capabilities(target)
        self.assertTrue(capabilities["inventory"]); self.assertFalse(capabilities["reconnect"])
        self.assertFalse(capabilities["preserve_selection"])
        inventory = await mcp.inventory(target, scope="session")
        self.assertEqual(inventory["servers"][0]["connection_status"], "unknown")
        with self.assertRaises(ConversationError):
            await mcp.reconnect(target, scope="session", operation_id="native-reconnect", expected_selection_digest="wrong")
        self.assertFalse(any(r["method"] == "config/mcpServer/reload" for r in self.calls))
