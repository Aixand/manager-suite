"""Chat controls against the installed OpenCode server and local model fixture."""

import asyncio
import json
import shutil
import unittest
from unittest.mock import AsyncMock, patch

import test_opencode_native as native
from conversation_manager import ConversationError


@unittest.skipUnless(shutil.which("opencode"), "Requires installed OpenCode")
class OpenCodeControlTests(unittest.IsolatedAsyncioTestCase):
    model_variants = [
        dict(
            id=name,
            settings=dict(reasoningEffort=effort, serviceTier=tier),
            body=dict(
                reasoning_effort=effort,
                service_tier="priority" if tier == "priority" else "auto",
            ),
        )
        for name, effort, tier in [
            ("low", "low", "default"),
            ("high", "high", "default"),
            ("low-fast", "low", "priority"),
            ("high-fast", "high", "priority"),
        ]
    ]
    asyncSetUp = native.OpenCodeNativeTests.asyncSetUp
    asyncTearDown = native.OpenCodeNativeTests.asyncTearDown
    wait = native.OpenCodeNativeTests.wait

    async def setting(self, key, value, operation=None):
        result = await self.core.mutate(
            "set_config",
            dict(
                session_ref=self.session,
                config_id=key,
                value=value,
                operation_id=operation or key + ":" + value,
            ),
        )
        self.assertEqual(result["status"], "completed", result)
        return {row["id"]: row for row in result["result"]["options"]}

    async def command(self, name, arguments="", operation=None):
        result = await self.core.mutate(
            "execute_command",
            dict(
                session_ref=self.session,
                name=name,
                arguments=arguments,
                operation_id=operation or "command:" + name,
            ),
        )
        self.assertEqual(result["status"], "completed", result)
        return result["result"]

    async def test_reasoning_changes_native_variant_and_actual_provider_request(self):
        initial = {
            row["id"]: row
            for row in (await self.core.adapter.configuration(self.session))["options"]
        }
        self.assertIn("effort", initial)
        values = {row["value"] for row in initial["effort"]["options"]}
        self.assertIn("high", values)
        changed = await self.setting("effort", "high")
        self.assertEqual(changed["effort"]["currentValue"], "high")
        self.assertEqual(changed["model"]["currentValue"], "fixture/fixture#high")
        self.release.set()
        sent = await self.core.mutate(
            "send",
            dict(
                session_ref=self.session,
                content="Use the selected effort",
                operation_id="reasoning-send",
            ),
        )
        self.assertEqual(sent["status"], "accepted", sent)
        await self.wait(lambda: not self.core.active)
        self.assertIn("high", json.dumps(self.requests[0]))
        self.assertEqual(
            (await self.setting("variant", "default"))["variant"]["currentValue"],
            "default",
        )

    async def test_speed_preserves_reasoning_and_reaches_native_model_request(self):
        await self.setting("effort", "high")
        options = await self.setting("serviceTier", "priority")
        self.assertEqual(options["effort"]["currentValue"], "high")
        self.assertEqual(options["serviceTier"]["currentValue"], "priority")
        self.assertEqual(options["model"]["currentValue"], "fixture/fixture#high-fast")
        options = await self.setting("effort", "low")
        self.assertEqual(options["serviceTier"]["currentValue"], "priority")
        self.release.set()
        sent = await self.core.mutate(
            "send",
            dict(
                session_ref=self.session,
                content="Use selected speed",
                operation_id="speed-send",
            ),
        )
        await self.wait(lambda: not self.core.active)
        self.assertEqual(self.requests[0]["service_tier"], "priority")
        self.assertEqual(self.requests[0]["reasoning_effort"], "low")

    async def test_native_compact_has_durable_turn_and_observed_completion(self):
        self.release.set()
        await self.core.mutate(
            "send",
            dict(
                session_ref=self.session,
                content="Initial context",
                operation_id="before-compact",
            ),
        )
        await self.wait(lambda: not self.core.active)
        result = await self.core.mutate(
            "execute_command",
            dict(
                session_ref=self.session,
                name="compact",
                arguments="",
                operation_id="compact",
            ),
        )
        self.assertEqual(result["status"], "accepted", result)
        await self.wait(lambda: not self.core.active)
        final = self.core.store.operation("compact")
        self.assertEqual(final["status"], "completed", final)
        self.assertEqual(final["result"]["status"], "completed", final)

    async def test_native_init_callback_observes_completion(self):
        import subprocess

        subprocess.run(["git", "-C", str(self.home), "init", "-q"], check=True)
        self.release.set()
        result = await self.core.mutate(
            "execute_command",
            dict(
                session_ref=self.session, name="init", arguments="", operation_id="init"
            ),
        )
        self.assertEqual(result["status"], "accepted", result)
        await self.wait(lambda: not self.core.active)
        final = self.core.store.operation("init")
        self.assertEqual(final["status"], "completed", final)
        self.assertEqual(final["result"]["status"], "completed", final)

    async def test_plan_and_permission_changes_are_enforced_by_native_rules(self):
        await self.setting("permissions", "ask")
        result = await self.setting("mode", "plan")
        self.assertEqual(result["mode"]["currentValue"], "plan")
        identity = self.session["native_session_id"]
        info = (await self.core.adapter.http("GET", "/api/session/" + identity))["data"]
        self.assertEqual(info["agent"], "plan")

        async def denied(action):
            result = await self.core.adapter.http(
                "POST",
                "/api/session/" + identity + "/permission",
                dict(
                    action=action,
                    resources=["file.txt"],
                    save=[],
                    metadata={},
                    source={
                        "type": "tool",
                        "messageID": "msg_fixture",
                        "id": "fixture",
                    },
                ),
            )
            self.assertEqual(result["data"]["effect"], "deny", result)

        await denied("edit")
        await self.setting("permissions", "allow")
        await denied("edit")
        changed = await self.setting("mode", "default")
        self.assertEqual(changed["permissions"]["currentValue"], "allow")
        result = await self.core.adapter.http(
            "POST",
            "/api/session/" + identity + "/permission",
            dict(
                action="edit",
                resources=["file.txt"],
                save=[],
                metadata={},
                source={"type": "tool", "messageID": "msg_fixture", "id": "fixture"},
            ),
        )
        self.assertEqual(result["data"]["effect"], "allow", result)
        await self.setting("permissions", "deny")
        await denied("shell")

    async def test_startup_policy_is_native_and_settings_survive_exact_load(self):
        created = await self.core.mutate(
            "create_session",
            dict(
                operation_id="planned-session",
                settings={"mode": "plan", "access": {"permission_mode": "deny"}},
            ),
        )
        self.assertEqual(created["status"], "completed", created)
        second = created["result"]["session_ref"]
        state = (
            await self.core.adapter.http(
                "GET", "/api/session/" + second["native_session_id"]
            )
        )["data"]
        self.assertEqual(state["agent"], "plan")
        self.assertIn(
            {"action": "*", "resource": "*", "effect": "deny"}, state["permissions"]
        )
        self.assertTrue(self.core.adapter.operations["access_delivery"]["supported"])
        await self.setting("effort", "high")
        await self.setting("permissions", "ask")
        saved = self.core.store.get("session", self.session["session_id"])["settings"]
        await self.core.adapter.load(self.session, saved)
        options = {
            row["id"]: row["currentValue"]
            for row in (await self.core.adapter.configuration(self.session))["options"]
        }
        self.assertEqual(options["effort"], "high")
        self.assertEqual(options["permissions"], "ask")

    async def test_native_read_commands_rename_and_replay_are_idle_and_do_not_infer(
        self,
    ):
        result = await self.command("rename", "Renamed in chat")
        self.assertEqual(result["title"], "Renamed in chat")
        self.assertIn("options", (await self.command("status"))["output"])
        self.assertIn("tokens", (await self.command("usage"))["output"])
        self.assertFalse(self.received.is_set())
        with patch.object(
            self.core.adapter,
            "http",
            AsyncMock(side_effect=AssertionError("mutation replayed")),
        ):
            self.assertEqual(
                (await self.command("rename", "Renamed in chat"))["title"],
                result["title"],
            )

    async def test_setting_slash_commands_reuse_native_controls_and_saved_receipts(
        self,
    ):
        self.assertIn("options", await self.command("reasoning", "high"))
        self.assertIn("options", await self.command("fast", "on"))
        configuration = (await self.command("status"))["output"]
        values = {row["id"]: row["currentValue"] for row in configuration["options"]}
        self.assertEqual(values["serviceTier"], "priority")
        self.assertEqual(values["effort"], "high")
        self.assertEqual((await self.command("model"))["output"]["id"], "model")
        await self.command("plan")
        await self.command("permissions", "deny")
        values = {
            row["id"]: row["currentValue"]
            for row in (await self.core.adapter.configuration(self.session))["options"]
        }
        self.assertEqual(values["mode"], "plan")
        self.assertEqual(values["permissions"], "deny")
        self.assertFalse(self.received.is_set())

    async def test_settings_update_excludes_concurrent_send_and_mutations(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.core.adapter.http

        async def held(method, route, body=None):
            if method == "POST" and route.endswith("/model"):
                entered.set()
                await release.wait()
            return await original(method, route, body)

        with patch.object(self.core.adapter, "http", held):
            changing = asyncio.create_task(
                self.setting("effort", "high", "held-setting")
            )
            await asyncio.wait_for(entered.wait(), 10)
            try:
                send = await self.core.mutate(
                    "send",
                    dict(
                        session_ref=self.session,
                        content="race",
                        operation_id="racing-send",
                    ),
                )
                self.assertEqual(send["error"]["code"], "busy")
                second = await self.core.mutate(
                    "set_config",
                    dict(
                        session_ref=self.session,
                        config_id="mode",
                        value="plan",
                        operation_id="racing-setting",
                    ),
                )
                self.assertEqual(second["error"]["code"], "busy")
                self.assertFalse(self.received.is_set())
            finally:
                release.set()
                await changing
        self.assertFalse(self.core.configuring)

    async def test_partial_policy_update_and_lost_readback_remain_uncertain(self):
        original = self.core.adapter.http

        async def fail_agent(method, route, body=None):
            if method == "POST" and route.endswith("/agent"):
                raise ConversationError("validation", "Agent change rejected")
            return await original(method, route, body)

        with patch.object(self.core.adapter, "http", fail_agent):
            result = await self.core.mutate(
                "set_config",
                dict(
                    session_ref=self.session,
                    config_id="mode",
                    value="plan",
                    operation_id="partial-policy",
                ),
            )
        self.assertEqual(result["status"], "uncertain", result)
        with patch.object(
            self.core.adapter, "http", AsyncMock(side_effect=AssertionError("replayed"))
        ):
            replay = await self.core.mutate(
                "set_config",
                dict(
                    session_ref=self.session,
                    config_id="mode",
                    value="plan",
                    operation_id="partial-policy",
                ),
            )
        self.assertEqual(replay, result)

    async def test_unknown_choices_and_active_turn_updates_never_dispatch(self):
        await self.setting("effort", "low")
        with patch.object(
            self.core.adapter, "http", wraps=self.core.adapter.http
        ) as request:
            result = await self.core.mutate(
                "set_config",
                dict(
                    session_ref=self.session,
                    config_id="effort",
                    value="invented",
                    operation_id="bad-effort",
                ),
            )
            self.assertNotEqual(result["status"], "completed")
            self.assertFalse(
                any(call.args[0] == "POST" for call in request.call_args_list)
            )
        sent = await self.core.mutate(
            "send",
            dict(session_ref=self.session, content="held", operation_id="active-send"),
        )
        await self.wait(self.received.is_set)
        result = await self.core.mutate(
            "set_config",
            dict(
                session_ref=self.session,
                config_id="mode",
                value="plan",
                operation_id="busy-mode",
            ),
        )
        self.assertEqual(result["error"]["code"], "busy")
        self.release.set()
        await self.wait(lambda: not self.core.active)
