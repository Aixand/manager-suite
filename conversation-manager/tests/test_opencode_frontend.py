"""Real OpenCode executable, Agent Manager, and frontend API; local model only."""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

try:
    from agent_manager import Manager
except ImportError:
    Manager = None
from conversation_manager import ConversationClient, ConversationError


@unittest.skipUnless(
    Manager and shutil.which("opencode"),
    "Requires Agent Manager and OpenCode V2",
)
class OpenCodeFrontendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cm-opencode-frontend-")
        self.root = Path(self.tmp.name).resolve()
        self.received = threading.Event()
        self.release = threading.Event()
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append(body)
                owner.received.set()
                owner.release.wait(10)
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    for delta, finish in [
                        (
                            {"role": "assistant", "content": "OPENCODE_FRONTEND_OK"},
                            None,
                        ),
                        ({}, "stop"),
                    ]:
                        row = dict(
                            id="fixture",
                            object="chat.completion.chunk",
                            created=1,
                            model="fixture",
                            choices=[dict(index=0, delta=delta, finish_reason=finish)],
                        )
                        self.wfile.write(("data: " + json.dumps(row) + "\n\n").encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.config = self.root / "providers.toml"
        self.config.write_text(
            "[providers.opencode]\nexecutable="
            + json.dumps(shutil.which("opencode"))
            + "\n"
        )
        self.manager = Manager(
            "opencode", config=self.config, root=self.root / "agents"
        )
        self.manager.new_profile("work")
        self.identifier = self.manager.resolve_profile("work").environment_id
        with self.manager.lease_native_environment(self.identifier) as lease:
            config_dir = Path(lease.env["OPENCODE_CONFIG_DIR"])
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "opencode.json").write_text(
            json.dumps(
                {
                    "providers": {
                        "fixture": {
                            "name": "fixture",
                            "package": "@ai-sdk/openai-compatible",
                            "settings": {
                                "baseURL": f"http://127.0.0.1:{self.server.server_port}/v1",
                                "apiKey": "fixture",
                            },
                            "models": {
                                "fixture": {
                                    "name": "fixture",
                                    "limit": {"context": 32000, "output": 1024},
                                }
                            },
                        }
                    }
                }
            )
        )
        self.client = ConversationClient(
            self.root / "state",
            agent_config=self.config,
            agent_root=self.root / "agents",
        )
        self.agent = (await self.client.available_agents(providers=("opencode",)))[0][
            "reference"
        ]
        self.conversations = []
        self.operations = []

    async def asyncTearDown(self):
        self.release.set()
        for conversation in self.conversations:
            await conversation.disconnect()
        for operation in self.operations:
            try:
                await self.client.stop_startup(
                    operation, operation_id="cleanup:" + operation
                )
            except ConversationError as error:
                if error.code != "not_found":
                    raise
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()
        self.tmp.cleanup()

    async def start(self, operation="start", cwd=None):
        self.operations.append(operation)
        conversation = await self.client.start_conversation(
            agent=self.agent,
            cwd=cwd or self.root,
            settings={"model": "fixture/fixture"},
            operation_id=operation,
        )
        self.conversations.append(conversation)
        return conversation

    async def test_start_stream_reconnect_and_shared_worker(self):
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "must-not-leak", "OPENCODE_DB": "/wrong/database"},
        ):
            first = await self.start()
        sent = await first.send("Reply briefly", operation_id="send")
        async with asyncio.timeout(20):
            while not self.received.is_set():
                await asyncio.sleep(0.02)
        await first.disconnect()
        attached = await self.client.attach_conversation(first.reference)
        self.conversations.append(attached)
        self.release.set()
        async with asyncio.timeout(20):
            async for event in attached.events():
                if event["kind"] == "turn.completed":
                    break
        result = await attached.turn_result(sent["result"]["turn_id"])
        self.assertEqual(result["status"], "completed", result)
        self.assertIn("OPENCODE_FRONTEND_OK", result["final_text"])
        second_work = self.root / "second-work"
        second_work.mkdir()
        second = await self.start("second", cwd=second_work)
        self.assertNotEqual(
            second.reference["native_session_id"], first.reference["native_session_id"]
        )
        self.assertEqual(second.reference["cwd"], str(second_work))
        self.assertEqual(
            first.startup_receipt["recovery"]["runtime_ref"],
            second.startup_receipt["recovery"]["runtime_ref"],
        )
        self.assertEqual(len(await self.client.conversations()), 2)
        self.assertEqual((await self.start()).reference, first.reference)
        self.assertNotIn(b"must-not-leak", self.client.store.path.read_bytes())

    async def test_cancel_active_native_turn(self):
        conversation = await self.start()
        sent = await conversation.send("Wait for fixture", operation_id="send")
        async with asyncio.timeout(20):
            while not self.received.is_set():
                await asyncio.sleep(0.02)
        await conversation.cancel(sent["result"]["turn_id"], operation_id="cancel")
        async with asyncio.timeout(20):
            async for event in conversation.events():
                if event["kind"] == "turn.completed":
                    break
        self.assertEqual(
            (await conversation.turn_result(sent["result"]["turn_id"]))["status"],
            "cancelled",
        )

    async def test_environment_guard_blocks_second_owner(self):
        await self.start()
        from agent_manager.errors import BusyError

        with self.assertRaises(BusyError):
            with self.manager.lease_native_environment(self.identifier):
                pass

    async def test_sdk_command_discovery_does_not_start_model_work(self):
        conversation = await self.start()
        listing = await conversation.list_slash_commands()
        self.assertEqual(listing["schema"], "conversation-manager/slash-commands-1")
        self.assertEqual(listing["state"], "ready")
        self.assertEqual(listing["source"], "opencode-native-api")
        self.assertEqual(listing["scope"], "registered-native-callbacks")
        rows = {row["name"]: row for row in listing["commands"]}
        self.assertTrue(
            {
                "review",
                "init",
                "compact",
                "rename",
                "status",
                "model",
                "permissions",
                "plan",
            }
            <= rows.keys()
        )
        self.assertTrue(rows["compact"]["executable"])
        self.assertEqual(rows["permissions"]["setting_id"], "permissions")
        self.assertTrue(rows["model"]["argument_options"])
        self.assertFalse(
            any(
                row["name"] == "goal"
                for row in (
                    await conversation.list_slash_commands(available_only=True)
                )["commands"]
            )
        )
        self.assertFalse(self.received.is_set())

    async def review_session(self):
        work = self.root / "review-work"
        work.mkdir()

        async def git(*arguments):
            process = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(work),
                *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, error = await process.communicate()
            self.assertEqual(process.returncode, 0, error.decode())

        await git("init", "-q")
        (work / "example.txt").write_text("before\n")
        await git("add", "example.txt")
        await git(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "Initial",
        )
        (work / "example.txt").write_text("after\n")
        conversation = await self.start(cwd=work)
        self.release.set()
        await conversation.send("Warm up the local fixture", operation_id="warmup")
        async with asyncio.timeout(20):
            async for event in conversation.events():
                if event["kind"] == "turn.completed":
                    break
        commands = await conversation.list_slash_commands(available_only=True)
        self.assertTrue(
            any(
                row["name"] == "review" and row["executable"]
                for row in commands["commands"]
            ),
            commands,
        )
        return conversation

    async def test_native_review_command_streams_and_keyed_retry_does_not_repeat(self):
        conversation = await self.review_session()
        sent = await conversation.command("review", "HEAD", operation_id="review")
        self.assertEqual(sent["status"], "accepted", sent)
        async with asyncio.timeout(20):
            async for event in conversation.events():
                if (
                    event["kind"] == "turn.completed"
                    and event["turn_id"] == sent["result"]["turn_id"]
                ):
                    break
        result = await conversation.turn_result(sent["result"]["turn_id"])
        self.assertEqual(result["status"], "completed", result)
        self.assertIn("OPENCODE_FRONTEND_OK", result["final_text"])
        requests = len(self.requests)
        retried = await conversation.command("review", "HEAD", operation_id="review")
        self.assertEqual(retried["result"]["turn_id"], sent["result"]["turn_id"])
        self.assertEqual(retried["status"], "completed")
        self.assertEqual(len(self.requests), requests)
        expanded = json.dumps(self.requests[-1])
        self.assertIn("HEAD", expanded)
        with self.assertRaises(ConversationError) as caught:
            await conversation.command("review", "", operation_id="review")
        self.assertEqual(caught.exception.code, "conflict")

    async def test_native_review_can_be_cancelled_after_model_work_starts(self):
        conversation = await self.review_session()
        self.received.clear()
        self.release.clear()
        sent = await conversation.command(
            "review", "HEAD", operation_id="review-cancel"
        )
        async with asyncio.timeout(20):
            while not self.received.is_set():
                await asyncio.sleep(0.02)
        await conversation.cancel(
            sent["result"]["turn_id"], operation_id="cancel-review"
        )
        async with asyncio.timeout(20):
            async for event in conversation.events():
                if (
                    event["kind"] == "turn.completed"
                    and event["turn_id"] == sent["result"]["turn_id"]
                ):
                    break
        self.assertEqual(
            (await conversation.turn_result(sent["result"]["turn_id"]))["status"],
            "cancelled",
        )

    async def test_concurrent_chats_eventually_share_one_worker(self):
        results = await asyncio.gather(
            self.start("one"), self.start("two"), return_exceptions=True
        )
        for index, result in enumerate(results):
            if isinstance(result, ConversationError):
                self.assertTrue(result.uncertain)
                self.assertEqual(result.code, "busy")
                results[index] = await self.start(("one", "two")[index])
            elif isinstance(result, BaseException):
                raise result
        self.assertEqual(
            results[0].startup_receipt["recovery"]["runtime_ref"],
            results[1].startup_receipt["recovery"]["runtime_ref"],
        )
        self.assertNotEqual(
            results[0].reference["native_session_id"],
            results[1].reference["native_session_id"],
        )

    async def test_stopped_shared_worker_is_replaced_only_for_new_startup(self):
        first = await self.start()
        original = first.startup_receipt["recovery"]["runtime_ref"]
        await self.client.stop_startup("start", operation_id="stop-first")
        with self.assertRaises(ConversationError):
            await self.start()
        second = await self.start("new-start")
        self.assertNotEqual(second.startup_receipt["recovery"]["runtime_ref"], original)

    async def test_closed_chat_does_not_stop_other_chat(self):
        first = await self.start()
        second = await self.start("second")
        closed = await first.close(operation_id="close-first")
        self.assertEqual(closed["status"], "completed")
        self.release.set()
        sent = await second.send("Other chat still works", operation_id="second-send")
        async with asyncio.timeout(20):
            async for event in second.events():
                if event["kind"] == "turn.completed":
                    break
        self.assertEqual(
            (await second.turn_result(sent["result"]["turn_id"]))["status"], "completed"
        )

    async def test_worker_exit_stops_server_before_environment_reuse(self):
        import signal
        from agent_manager.errors import BusyError
        from aixand_local_runtime import process_start

        conversation = await self.start()
        runtime = conversation.startup_receipt["recovery"]["runtime_ref"]
        self.assertEqual(process_start(runtime["pid"]), runtime["start_ticks"])
        os.kill(runtime["pid"], signal.SIGTERM)
        async with asyncio.timeout(10):
            while True:
                try:
                    with self.manager.lease_native_environment(self.identifier):
                        break
                except BusyError:
                    await asyncio.sleep(.02)
        # Windows refuses removal while a surviving server holds the database.
        with self.manager.lease_native_environment(self.identifier) as lease:
            database = Path(lease.env["OPENCODE_DB"])
            if database.exists():
                moved = database.with_suffix(".released")
                database.rename(moved)
                moved.rename(database)
        # The lease may become available after server cleanup but just before
        # the worker finishes its remaining asynchronous teardown.
        async with asyncio.timeout(10):
            while process_start(runtime["pid"]) == runtime["start_ticks"]:
                await asyncio.sleep(.02)

    async def test_reboot_restore_keeps_history_without_model_replay(self):
        self.release.set()
        original = await self.start()
        await original.send('Before reboot', operation_id='before-reboot')
        async with asyncio.timeout(20):
            while (await original.state()).get('active_turn'):
                await asyncio.sleep(.02)
        history = await self.client.history(original.reference)
        calls = len(self.requests)
        await original.disconnect()
        await self.client.stop_startup('start', operation_id='reboot-stop')
        fresh = ConversationClient(self.client.home, agent_config=self.config, agent_root=self.root / 'agents')
        restored = await fresh.resume_conversation(original.reference, operation_id='resume-reboot')
        self.conversations.append(restored)
        self.operations.append('resume-reboot')
        self.assertEqual(restored.reference['native_session_id'], original.reference['native_session_id'])
        self.assertNotEqual(restored.reference['connection_id'], original.reference['connection_id'])
        self.assertEqual((await fresh.history(restored.reference))['entries'], history['entries'])
        self.assertEqual(len(self.requests), calls)
        self.assertFalse((await restored.state()).get('active_turn'))
        await restored.send('After reboot', operation_id='after-reboot')
        async with asyncio.timeout(20):
            while (await restored.state()).get('active_turn'):
                await asyncio.sleep(.02)
        self.assertEqual(len(self.requests), calls + 1)

    async def test_two_saved_chats_restore_into_one_shared_worker(self):
        first = await self.start()
        second_directory = self.root / 'second-after-reboot'
        second_directory.mkdir()
        second = await self.start('second-before-reboot', cwd=second_directory)
        await self.client.stop_startup('start', operation_id='stop-shared-before-reboot')
        fresh = ConversationClient(self.client.home, agent_config=self.config, agent_root=self.root / 'agents')
        restored_first = await fresh.resume_conversation(first.reference, operation_id='resume-shared-first')
        self.conversations.append(restored_first)
        self.operations.append('resume-shared-first')
        restored_second = await fresh.resume_conversation(second.reference, operation_id='resume-shared-second')
        self.conversations.append(restored_second)
        self.operations.append('resume-shared-second')
        self.assertEqual(restored_first.reference['connection_id'], restored_second.reference['connection_id'])
        self.assertEqual(restored_first.reference['native_session_id'], first.reference['native_session_id'])
        self.assertEqual(restored_second.reference['native_session_id'], second.reference['native_session_id'])
        self.assertEqual(restored_second.reference['cwd'], str(second_directory.resolve()))
        self.assertFalse(self.requests)
