"""Installed OpenCode with a local model fixture; no account or paid requests."""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest

from conversation_manager import PreparedLaunch
from conversation_manager.core import Core
from conversation_manager.store import Store


@unittest.skipUnless(shutil.which("opencode"), "OpenCode V2 executable is unavailable")
class OpenCodeNativeTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_required_mcp_does_not_create_a_tool_less_session(self):
        from dataclasses import replace

        prepared = replace(
            self.prepared,
            env={
                **self.prepared.env,
                "OPENCODE_DB": str(self.home / "failed-mcp/opencode.db"),
            },
            settings={
                "model": "fixture/fixture",
                "mcp": {
                    "servers": [
                        {
                            "server_name": "failed_fixture",
                            "command": sys.executable,
                            "args": ["-c", "raise SystemExit(3)"],
                            "env_vars": [],
                        }
                    ]
                },
            },
        )
        core = Core(
            Store(self.home / "failed-manager"),
            prepared,
            {**self.core.ref, "connection_id": "failed-mcp"},
        )
        await core.adapter.start()
        try:
            result = await core.mutate(
                "create_session", {"operation_id": "failed-create"}
            )
            self.assertEqual(result["status"], "failed", result)
            self.assertEqual(result["error"]["code"], "resource_unavailable")
            self.assertEqual(core.sessions, {})
        finally:
            await core.adapter.close()

    async def test_failed_model_turn_preserves_bounded_error_without_response_body(
        self,
    ):
        def fail(handler):
            handler.rfile.read(int(handler.headers["Content-Length"]))
            handler.send_response(400)
            handler.send_header("Content-Type", "application/json")
            handler.end_headers()
            handler.wfile.write(
                b'{"error":{"message":"CM_FAILURE_DETAIL","type":"invalid_request_error"}}'
            )

        self.server.RequestHandlerClass.do_POST = fail
        sent = await self.send("model-failure")
        await self.wait(lambda: not self.core.active)
        result = self.core.store.turn_result(self.session, sent["result"]["turn_id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["message"], "CM_FAILURE_DETAIL")
        self.assertEqual(result["error"]["status"], 400)
        self.assertEqual(result["error"]["code"], "provider.invalid-request")
        self.assertNotIn("response", result["error"])

    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="native-opencode-")
        self.home = Path(self.temporary.name).resolve()
        self.received, self.release = threading.Event(), threading.Event()
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append(body)
                owner.received.set()
                owner.release.wait(15)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    for delta, finish in [
                        ({"role": "assistant", "content": "fixture completed"}, None),
                        ({}, "stop"),
                    ]:
                        payload = {
                            "id": "fixture",
                            "object": "chat.completion.chunk",
                            "created": 1,
                            "model": "fixture",
                            "choices": [
                                {"index": 0, "delta": delta, "finish_reason": finish}
                            ],
                        }
                        self.wfile.write(
                            ("data: " + json.dumps(payload) + "\n\n").encode()
                        )
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        env = {
            key: os.environ[key]
            for key in ("PATH", "HOME", "SYSTEMROOT")
            if key in os.environ
        }
        env.update(
            {
                key: str(self.home / key)
                for key in (
                    "XDG_CONFIG_HOME",
                    "XDG_DATA_HOME",
                    "XDG_STATE_HOME",
                    "XDG_CACHE_HOME",
                    "OPENCODE_CONFIG_DIR",
                )
            }
        )
        env["OPENCODE_DB"] = str(self.home / "data/opencode.db")
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(
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
        if getattr(self, "model_variants", None):
            inline = json.loads(env["OPENCODE_CONFIG_CONTENT"])
            inline["providers"]["fixture"]["models"]["fixture"]["variants"] = (
                self.model_variants
            )
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps(inline)
        self.prepared = PreparedLaunch(
            backend_id="opencode",
            adapter_id="opencode-native",
            protocol="opencode-v2",
            argv=[shutil.which("opencode")],
            transport={"kind": "opencode-http"},
            cwd=str(self.home),
            settings={"model": "fixture/fixture"},
            env=env,
        )
        ref = dict(
            schema="conversation-manager/connection-ref-1",
            connection_id="native-test",
            backend_id="opencode",
            adapter_id="opencode-native",
            protocol="opencode-v2",
            runtime_ref={"owner": "fixture", "id": "test"},
            transport={"kind": "fixture"},
        )
        self.core = Core(Store(self.home / "manager"), self.prepared, ref)
        await self.core.adapter.start()
        created = await self.core.mutate("create_session", {"operation_id": "create"})
        self.assertEqual(created["status"], "completed", created)
        self.session = created["result"]["session_ref"]

    async def asyncTearDown(self):
        self.release.set()
        await self.core.adapter.close()
        await asyncio.gather(*self.core.tasks, return_exceptions=True)
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()
        self.temporary.cleanup()

    async def wait(self, predicate):
        async with asyncio.timeout(20):
            while not predicate():
                await asyncio.sleep(0.02)

    async def send(self, key="send"):
        return await self.core.mutate(
            "send",
            {
                "operation_id": key,
                "session_ref": self.session,
                "content": "original request",
            },
        )

    async def test_steering_is_consumed_in_same_drain_and_retry_does_not_duplicate(
        self,
    ):
        sent = await self.send()
        await self.wait(self.received.is_set)
        for key, text in [
            ("first", "first distinct correction"),
            ("second", "second distinct correction"),
        ]:
            args = {
                "operation_id": key,
                "session_ref": self.session,
                "expected_turn_id": sent["result"]["turn_id"],
                "text": text,
            }
            first = await self.core.mutate("steer", args)
            self.assertEqual(first["status"], "completed", first)
            self.assertEqual(await self.core.mutate("steer", args), first)
        self.release.set()
        await self.wait(lambda: not self.core.active)
        messages = await self.core.adapter.messages(self.session["native_session_id"])
        self.assertEqual(
            [row["text"] for row in messages if row["type"] == "user"],
            [
                "original request",
                "first distinct correction",
                "second distinct correction",
            ],
        )
        self.assertEqual(len([row for row in messages if row["type"] == "idle"]), 1)
        self.assertTrue(
            any(
                "second distinct correction" in json.dumps(row) for row in self.requests
            )
        )
        history = self.core.store.history_page(self.session)
        inputs = [row for row in history["entries"] if row["kind"] == "message.input"]
        self.assertEqual(len(inputs), 2)
        self.assertTrue(
            all(row["turn_id"] == sent["result"]["turn_id"] for row in inputs)
        )

    async def test_stop_before_native_admission_cannot_start_a_prompt_later(self):
        sent = await self.send()
        stopped = await self.core.mutate(
            "cancel",
            {
                "operation_id": "stop",
                "session_ref": self.session,
                "turn_id": sent["result"]["turn_id"],
            },
        )
        self.assertIn(stopped["status"], ("accepted", "completed"))
        await self.wait(lambda: not self.core.active)
        self.assertEqual(
            self.core.store.operation("send")["result"]["status"], "cancelled"
        )
        self.assertEqual(
            await self.core.adapter.messages(self.session["native_session_id"]), []
        )
        self.assertFalse(self.received.is_set())

    async def test_restore_preserves_exact_identity_and_excludes_second_database_owner(
        self,
    ):
        from conversation_manager.adapters.opencode import OpenCodeAdapter
        from conversation_manager import ConversationError

        second = OpenCodeAdapter(self.core, self.prepared)
        await second.start()
        with self.assertRaisesRegex(ConversationError, "already has an owner"):
            await second.load(
                self.session, {"cwd": str(self.home), "model": "fixture/fixture"}
            )
        await self.core.adapter.close()
        try:
            identity, observed = await second.restore(
                self.session, {"cwd": str(self.home), "model": "fixture/fixture"}
            )
            self.assertEqual(identity, self.session["native_session_id"])
            self.assertEqual(observed["session_id"], identity)
        finally:
            await second.close()

    async def test_native_form_answers_are_validated_before_reservation_and_acknowledged(
        self,
    ):
        await self.send()
        await self.wait(self.received.is_set)
        identity = self.session["native_session_id"]
        await self.core.adapter.http(
            "POST",
            "/api/session/" + identity + "/form",
            {
                "title": "Fixture question",
                "fields": [
                    {
                        "key": "answer",
                        "type": "boolean",
                        "title": "Continue?",
                        "required": True,
                    }
                ],
            },
        )
        await self.wait(lambda: bool(self.core.requests))
        request_id = next(iter(self.core.requests))
        invalid = await self.core.mutate(
            "respond",
            {
                "operation_id": "invalid-answer",
                "request_id": request_id,
                "response": {"native_result": {"answers": {"answer": "not a boolean"}}},
            },
        )
        self.assertEqual(invalid["error"]["code"], "validation")
        self.assertEqual(self.core.requests[request_id]["record"]["state"], "pending")
        valid = await self.core.mutate(
            "respond",
            {
                "operation_id": "valid-answer",
                "request_id": request_id,
                "response": {"native_result": {"answers": {"answer": "true"}}},
            },
        )
        self.assertIn(valid["status"], ("accepted", "completed"))
        await self.wait(
            lambda: self.core.store.operation("valid-answer")["status"] == "completed"
        )
        self.assertEqual(
            (await self.core.adapter.http("GET", "/api/session/" + identity + "/form"))[
                "data"
            ],
            [],
        )
        self.release.set()
        await self.wait(lambda: not self.core.active)

    async def test_uncertain_callback_is_not_resent_and_blocks_new_work(self):
        from conversation_manager import ConversationError

        adapter = self.core.adapter
        original = adapter.http
        dispatches = []

        async def lost(method, path, body=None):
            if path.startswith("/api/command"):
                return {"data": [{"name": "review"}]}
            if path.endswith("/command"):
                dispatches.append(body)
                raise ConversationError(
                    "transport", "Callback acknowledgement lost", uncertain=True
                )
            return await original(method, path, body)

        adapter.http = lost
        params = {
            "operation_id": "lost-review",
            "session_ref": self.session,
            "name": "review",
            "arguments": "HEAD",
        }
        sent = await self.core.mutate("execute_command", params)
        self.assertEqual(sent["status"], "accepted")
        await self.wait(lambda: not self.core.active)
        uncertain = self.core.store.operation("lost-review")
        self.assertEqual(uncertain["status"], "uncertain")
        self.assertEqual(await self.core.mutate("execute_command", params), uncertain)
        self.assertEqual(
            dispatches, [{"name": "review", "text": "HEAD", "delivery": "steer"}]
        )
        blocked = await self.core.mutate(
            "send",
            {
                "operation_id": "after-unknown",
                "session_ref": self.session,
                "content": "more",
            },
        )
        self.assertEqual(blocked["status"], "uncertain")

    async def test_callback_acknowledgement_without_completion_stays_uncertain(self):
        adapter = self.core.adapter
        original = adapter.http
        dispatches = []

        async def acknowledged(method, path, body=None):
            if path.startswith("/api/command"):
                return {"data": [{"name": "review"}]}
            if path.endswith("/command"):
                dispatches.append((path, body))
                return {}  # Native 204: no observed session work or completion.
            return await original(method, path, body)

        adapter.http = acknowledged
        adapter.command_timeout = 0.05
        params = {
            "operation_id": "no-completion",
            "session_ref": self.session,
            "name": "review",
            "arguments": "",
        }
        self.assertEqual(
            (await self.core.mutate("execute_command", params))["status"], "accepted"
        )
        await self.wait(lambda: not self.core.active)
        self.assertEqual(
            self.core.store.operation("no-completion")["status"], "uncertain"
        )
        self.assertEqual(
            (await self.core.mutate("execute_command", params))["status"], "uncertain"
        )
        self.assertEqual(len(dispatches), 1)
        self.assertEqual(
            dispatches[0][0],
            "/api/session/" + self.session["native_session_id"] + "/command",
        )
        self.assertFalse(self.received.is_set())

    async def test_unadvertised_review_is_rejected_without_native_dispatch(self):
        adapter = self.core.adapter
        original = adapter.http

        async def absent(method, path, body=None):
            if path.startswith("/api/command"):
                return {"data": []}
            self.assertFalse(path.endswith("/command"))
            return await original(method, path, body)

        adapter.http = absent
        params = {
            "operation_id": "absent-review",
            "session_ref": self.session,
            "name": "review",
            "arguments": "",
        }
        await self.core.mutate("execute_command", params)
        await self.wait(lambda: not self.core.active)
        observed = self.core.store.operation("absent-review")
        self.assertEqual(observed["status"], "failed")
        self.assertEqual(observed["error"]["code"], "unsupported")
        self.assertFalse(self.received.is_set())

    async def test_read_failure_after_callback_handoff_is_uncertain(self):
        from conversation_manager import ConversationError

        adapter = self.core.adapter
        original = adapter.http
        dispatches = []

        async def broken_read(method, path, body=None):
            if path.startswith("/api/command"):
                return {"data": [{"name": "review"}]}
            if path.endswith("/command"):
                dispatches.append(body)
                return {}
            if dispatches and "/message?" in path:
                raise ConversationError("transport", "History read lost")
            return await original(method, path, body)

        adapter.http = broken_read
        params = {
            "operation_id": "read-lost",
            "session_ref": self.session,
            "name": "review",
            "arguments": "",
        }
        await self.core.mutate("execute_command", params)
        await self.wait(lambda: not self.core.active)
        self.assertEqual(self.core.store.operation("read-lost")["status"], "uncertain")
        self.assertEqual(
            (await self.core.mutate("execute_command", params))["status"], "uncertain"
        )
        self.assertEqual(len(dispatches), 1)

    async def test_native_review_permission_denial_uses_existing_request_flow(self):
        import subprocess

        subprocess.run(["git", "-C", str(self.home), "init", "-q"], check=True)
        (self.home / "example.txt").write_text("before\n")
        subprocess.run(["git", "-C", str(self.home), "add", "example.txt"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.home),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-qm",
                "Initial",
            ],
            check=True,
        )
        (self.home / "example.txt").write_text("after\n")
        self.release.set()
        await self.send("warmup")
        await self.wait(lambda: not self.core.active)
        self.release.clear()
        self.received.clear()
        review = await self.core.mutate(
            "execute_command",
            {
                "operation_id": "review",
                "session_ref": self.session,
                "name": "review",
                "arguments": "HEAD",
            },
        )
        self.assertEqual(review["status"], "accepted")
        await self.wait(self.received.is_set)
        await self.core.adapter.http(
            "PATCH",
            "/api/session/" + self.session["native_session_id"],
            {"permissions": [{"action": "edit", "resource": "*", "effect": "ask"}]},
        )
        pending = asyncio.create_task(
            self.core.adapter.http(
                "POST",
                "/api/session/" + self.session["native_session_id"] + "/permission",
                {"action": "edit", "resources": [str(self.home / "example.txt")]},
            )
        )
        try:
            await self.wait(lambda: bool(self.core.requests))
            request_id = next(iter(self.core.requests))
            answered = await self.core.mutate(
                "respond",
                {
                    "operation_id": "deny-review",
                    "request_id": request_id,
                    "response": {"outcome": "selected", "option_id": "reject"},
                },
            )
            self.assertIn(answered["status"], ("accepted", "completed"))
            await self.wait(
                lambda: (
                    self.core.store.operation("deny-review")["status"] == "completed"
                )
            )
        finally:
            self.release.set()
            await asyncio.gather(pending, return_exceptions=True)
        await self.wait(lambda: not self.core.active)
        self.assertEqual(self.core.store.operation("review")["status"], "completed")
