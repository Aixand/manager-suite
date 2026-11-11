"""Command qualification, retained execution, and confirmed SDK settings."""

import asyncio
import unittest

from conversation_manager.adapters.sdk_commands import catalog
import test_sdk_backends as fixtures


class CommandCatalogTests(unittest.TestCase):
    def test_catalog_preserves_captured_skill_names_and_blocks_unsafe_controls(self):
        commands = catalog(
            "claude-agent-sdk",
            {"skills": [{"name": "audit"}]},
            [
                {"name": "compact"},
                {"name": "manager-resources:audit"},
                {"name": "other:unselected"},
                {"name": "clear"},
                {"name": "loop"},
                {"name": "verify"},
                {"name": "theme"},
                {"name": "../bad"},
            ],
            ["theme"],
        )
        rows = {row["name"]: row for row in commands["commands"]}
        for name in (
            "compact",
            "manager-resources:audit",
            "verify",
            "context",
            "usage",
            "mcp",
            "skills",
        ):
            self.assertEqual(rows[name]["execution"], "native")
        for name in ("other:unselected", "clear", "loop", "theme"):
            self.assertEqual(rows[name]["execution"], "unsupported")
            self.assertTrue(rows[name]["reason"])
        self.assertNotIn("../bad", rows)


class RetainedCommandTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.SdkBackendTests.asyncSetUp
    asyncTearDown = fixtures.SdkBackendTests.asyncTearDown
    key = fixtures.SdkBackendTests.key
    connection = fixtures.SdkBackendTests.connection
    session = fixtures.SdkBackendTests.session
    completion = fixtures.SdkBackendTests.completion

    async def test_discovery_inspection_and_rejection_do_not_send_prompts(self):
        for protocol in ("claude-agent-sdk", "antigravity-sdk"):
            connection = await self.connection(protocol)
            session = await self.session(connection)
            self.assertEqual((await connection.commands(session))["state"], "ready")
            for name in ("context", "usage", "mcp", "skills"):
                result = await connection.command(
                    session, name, operation_id=self.key()
                )
                self.assertEqual(result["status"], "completed", result)
            for name in ("invented", "loop", "clear"):
                result = await connection.command(
                    session, name, operation_id=self.key()
                )
                self.assertEqual(result["status"], "unsupported", result)
            result = await connection.send(
                session,
                "/compact" if protocol == "antigravity-sdk" else "/clear",
                operation_id=self.key(),
            )
            self.assertEqual(result["status"], "unsupported")
            result = await connection.command(
                session, "context", "unexpected", operation_id=self.key()
            )
            self.assertEqual(result["error"]["code"], "validation")
            output = (
                await connection.command(session, "context", operation_id=self.key())
            )["result"]["output"]
            self.assertEqual(output["turn_count"], 0)

    async def test_compact_has_one_retained_turn_and_survives_detach(self):
        connection = await self.connection()
        session = await self.session(connection)
        key = self.key()
        accepted = await connection.command(
            session, "compact", "keep the goal", operation_id=key
        )
        self.assertEqual(accepted["status"], "accepted")
        await connection.disconnect()
        await connection.reconnect()
        self.assertEqual((await self.completion(key))["status"], "completed")
        duplicate = await connection.command(
            session, "compact", "keep the goal", operation_id=key
        )
        self.assertEqual(duplicate["status"], "completed")
        history = (await connection.history_page(session))["entries"]
        self.assertEqual(
            len([row for row in history if row["kind"] == "command.output"]), 1
        )
        self.assertEqual(
            len([row for row in history if row["kind"] == "session.compacted"]), 1
        )
        output = (
            await connection.command(session, "context", operation_id=self.key())
        )["result"]["output"]
        self.assertEqual(output["prompts"], ["/compact keep the goal"])

    async def test_settings_are_idle_guarded_and_persisted_for_restore(self):
        for protocol in ("claude-agent-sdk", "antigravity-sdk"):
            connection = await self.connection(protocol)
            session = await self.session(connection)
            key = self.key()
            sent = await connection.send(session, "hold", operation_id=key)
            changed = await connection.set_config(
                session, "permission_mode", "deny", operation_id=self.key()
            )
            self.assertEqual(changed["error"]["code"], "busy")
            inspected = await asyncio.wait_for(
                connection.command(session, "usage", operation_id=self.key()), 1
            )
            self.assertEqual(inspected["status"], "completed")
            await connection.cancel(
                session, sent["result"]["turn_id"], operation_id=self.key()
            )
            await self.completion(key)
            changed = await connection.set_config(
                session, "permission_mode", "deny", operation_id=self.key()
            )
            self.assertEqual(changed["status"], "completed")
            if protocol == "claude-agent-sdk":
                self.assertEqual(
                    (
                        await connection.set_config(
                            session, "mode", "plan", operation_id=self.key()
                        )
                    )["status"],
                    "completed",
                )
            saved = self.client.store.get("session", session["session_id"])["settings"]
            self.assertEqual(saved["access"]["permission_mode"], "deny")
            if protocol == "claude-agent-sdk":
                self.assertEqual(saved["mode"], "plan")
            sent_key = self.key()
            await connection.send(session, "permission", operation_id=sent_key)
            self.assertEqual((await self.completion(sent_key))["status"], "completed")
            self.assertEqual(await connection.pending_requests(session), [])
