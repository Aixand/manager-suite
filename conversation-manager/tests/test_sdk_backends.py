import asyncio
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from conversation_manager import Client, PreparedLaunch, StandaloneLauncher, ConversationError


class SdkBackendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sdk-retained-")
        self.client = Client(self.tmp.name)
        self.launcher = StandaloneLauncher(
            self.tmp.name, service_factory="conversation_manager.fixture_sdk:factory"
        )
        self.refs, self.connections = [], []

    async def asyncTearDown(self):
        for connection in self.connections:
            await connection.disconnect()
        for ref in self.refs:
            await self.launcher.terminate(
                ref["runtime_ref"], operation_id="stop:" + ref["connection_id"]
            )
        self.tmp.cleanup()

    def key(self):
        return uuid.uuid4().hex

    async def connection(self, protocol="claude-agent-sdk", settings=None):
        claude = protocol == "claude-agent-sdk"
        prepared = PreparedLaunch(
            backend_id="copilot" if protocol == "copilot-sdk" else "claude" if claude else "antigravity",
            adapter_id="copilot-sdk" if protocol == "copilot-sdk" else "claude-sdk" if claude else "antigravity-sdk",
            protocol=protocol,
            argv=[],
            transport={"kind": "sdk"},
            cwd=self.tmp.name,
            settings=settings or {},
            env={
                "ANTHROPIC_API_KEY"
                if claude
                else "GEMINI_API_KEY": "private-fixture-key"
            },
        )
        launch = await self.client.launch(
            prepared, launcher=self.launcher, operation_id=self.key()
        )
        self.assertEqual(launch["status"], "completed", launch)
        ref = launch["result"]["connection_ref"]
        self.refs.append(ref)
        connection = await self.client.connect(ref)
        self.connections.append(connection)
        return connection

    async def session(self, connection):
        result = await connection.create_session({}, operation_id=self.key())
        self.assertEqual(result["status"], "completed", result)
        return result["result"]["session_ref"]

    async def completion(self, operation):
        async with asyncio.timeout(5):
            while True:
                result = await self.client.operation(operation)
                if result["status"] != "accepted":
                    return result
                await asyncio.sleep(0.02)

    async def pending(self, connection, session):
        async with asyncio.timeout(5):
            while True:
                rows = await connection.pending_requests(session)
                if rows:
                    return rows[0]
                await asyncio.sleep(0.02)

    async def test_both_sdk_sessions_keep_context_after_detach_and_do_not_replay(self):
        for protocol in ("claude-agent-sdk", "antigravity-sdk", "copilot-sdk"):
            connection = await self.connection(protocol)
            session = await self.session(connection)
            first = self.key()
            await connection.send(session, "remember", operation_id=first)
            self.assertEqual((await self.completion(first))["status"], "completed")
            await connection.disconnect()
            await connection.reconnect()
            second = self.key()
            await connection.send(session, "recall", operation_id=second)
            self.assertEqual((await self.completion(second))["status"], "completed")
            await connection.send(session, "recall", operation_id=second)
            history = await connection.history_page(session)
            self.assertEqual(
                [
                    r["payload"]["text"]
                    for r in history["entries"]
                    if r["kind"] == "message.delta" and r["payload"].get("role") == "assistant"
                ].count("remember"),
                2,
            )

    async def test_known_sdk_rejection_retains_an_exact_failed_terminal_artifact(self):
        connection = await self.connection()
        session = await self.session(connection)
        key = self.key()
        accepted = await connection.send(session, 'known-sdk-rejection', operation_id=key)
        completed = await self.completion(key)
        self.assertEqual(completed['status'], 'failed')
        artifact = await connection.turn_result(session, accepted['result']['turn_id'])
        self.assertEqual(artifact['status'], 'failed')
        self.assertEqual(artifact['error']['code'], 'provider_rejected')
        self.assertIsNone(artifact['final_text'])
        self.assertFalse(artifact['truncated'])

    async def test_approval_submitted_once_then_observed_and_denial_is_not_approval(
        self,
    ):
        for choice in ("allow_once", "deny_once"):
            connection = await self.connection()
            session = await self.session(connection)
            sent = self.key()
            await connection.send(session, "permission", operation_id=sent)
            request = await self.pending(connection, session)
            await connection.disconnect()
            await connection.reconnect()
            answered = self.key()
            result = await connection.respond(
                request["request_id"],
                {"outcome": "selected", "option_id": choice},
                operation_id=answered,
            )
            self.assertEqual(result["status"], "accepted")
            self.assertEqual((await self.completion(sent))["status"], "completed")
            self.assertEqual((await self.completion(answered))["status"], "completed")
            duplicate = await connection.respond(
                request["request_id"],
                {"outcome": "selected", "option_id": choice},
                operation_id=self.key(),
            )
            self.assertEqual(duplicate["error"]["code"], "conflict")
            history = await connection.history_page(session)
            self.assertIn(
                "approved" if choice == "allow_once" else "denied",
                [r["payload"].get("text") for r in history["entries"]],
            )

    async def test_cancel_pending_permission_is_observed_and_never_auto_approves(self):
        connection = await self.connection("antigravity-sdk")
        session = await self.session(connection)
        key = self.key()
        sent = await connection.send(session, "permission", operation_id=key)
        await self.pending(connection, session)
        cancel = self.key()
        await connection.cancel(session, sent["result"]["turn_id"], operation_id=cancel)
        self.assertEqual((await self.completion(key))["result"]["status"], "cancelled")
        self.assertTrue(
            (await self.completion(cancel))["result"]["cancellation_observed"]
        )
        self.assertEqual(await connection.pending_requests(session), [])

    async def test_model_change_requires_idle_and_closed_sessions_reject_new_work(self):
        connection = await self.connection()
        session = await self.session(connection)
        operation = self.key()
        sent = await connection.send(session, "hold", operation_id=operation)
        changed = await connection.set_model(
            session, "fixture-two", operation_id=self.key()
        )
        self.assertEqual(changed["error"]["code"], "busy")
        await connection.cancel(
            session, sent["result"]["turn_id"], operation_id=self.key()
        )
        await self.completion(operation)
        changed = await connection.set_model(
            session, "fixture-two", operation_id=self.key()
        )
        self.assertEqual(changed["status"], "completed")
        self.assertEqual(
            (await connection.configuration(session))["options"][0]["currentValue"],
            "fixture-two",
        )
        for _ in range(2):
            self.assertEqual(
                (await connection.close_session(session, operation_id=self.key()))[
                    "status"
                ],
                "completed",
            )
        sent = await connection.send(
            session, "do not dispatch", operation_id=self.key()
        )
        self.assertEqual(sent["status"], "failed")
        self.assertEqual(sent["error"]["code"], "conflict")

    async def test_user_question_is_validated_and_redaction_reaches_history(self):
        connection = await self.connection()
        session = await self.session(connection)
        operation = self.key()
        await connection.send(session, "question", operation_id=operation)
        request = await self.pending(connection, session)
        invalid = await connection.respond(
            request["request_id"],
            {"native_result": {"answers": {"choice": 123}}},
            operation_id=self.key(),
        )
        self.assertEqual(invalid["error"]["code"], "validation")
        await connection.respond(
            request["request_id"],
            {"native_result": {"answers": {"choice": "test"}}},
            operation_id=self.key(),
        )
        self.assertEqual((await self.completion(operation))["status"], "completed")
        secret = self.key()
        await connection.send(session, "secret", operation_id=secret)
        await self.completion(secret)
        self.assertNotIn(
            "private-fixture-key", json.dumps(await connection.history_page(session))
        )

    async def test_limit_is_failure_and_drain_allows_cancellation_but_blocks_new_work(
        self,
    ):
        connection = await self.connection()
        session = await self.session(connection)
        limited = self.key()
        await connection.send(session, "limit", operation_id=limited)
        self.assertEqual((await self.completion(limited))["status"], "failed")
        held = self.key()
        sent = await connection.send(session, "hold", operation_id=held)
        await connection.begin_drain(operation_id=self.key())
        self.assertTrue((await connection.drain_status())["blockers"])
        blocked = await connection.send(session, "new", operation_id=self.key())
        self.assertEqual(blocked["error"]["code"], "auth_recovery_pending")
        await connection.cancel(
            session, sent["result"]["turn_id"], operation_id=self.key()
        )
        await self.completion(held)
        self.assertEqual(
            (await connection.prepare_stop(operation_id=self.key()))["status"],
            "completed",
        )

    async def test_restore_requires_stopped_owner_and_preserves_provider_identity_and_history(
        self,
    ):
        for protocol in ("claude-agent-sdk", "antigravity-sdk", "copilot-sdk"):
            original = await self.connection(protocol)
            session = await self.session(original)
            sent = self.key()
            await original.send(session, "original", operation_id=sent)
            await self.completion(sent)
            replacement = await self.connection(protocol)
            with self.assertRaises(ConversationError):
                await replacement.restore_session(
                    session, launcher=self.launcher, operation_id=self.key()
                )
            await self.launcher.terminate(
                original.ref["runtime_ref"], operation_id=self.key()
            )
            operation = self.key()
            restored = await replacement.restore_session(
                session, launcher=self.launcher, operation_id=operation
            )
            self.assertEqual(restored["status"], "completed", restored)
            reference = restored["result"]["session_ref"]
            self.assertEqual(
                reference["native_session_id"], session["native_session_id"]
            )
            self.assertNotEqual(reference["connection_id"], session["connection_id"])
            self.assertEqual(
                await replacement.restore_session(
                    session, launcher=self.launcher, operation_id=operation
                ),
                restored,
            )
            recall = self.key()
            await replacement.send(reference, "recall", operation_id=recall)
            await self.completion(recall)
            history = await replacement.history_page(reference, limit=2)
            self.assertTrue(history["has_more"])
            self.assertEqual(history["entries"][0]["payload"]["text"], "original")

    async def test_portable_descriptor_rejects_sdk_argv_and_raw_credentials(self):
        with self.assertRaises(ConversationError):
            PreparedLaunch(
                backend_id="claude",
                adapter_id="claude-sdk",
                protocol="claude-agent-sdk",
                argv=["claude"],
                cwd=self.tmp.name,
            ).to_dict()
