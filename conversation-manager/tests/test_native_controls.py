import unittest
from unittest.mock import AsyncMock

from conversation_manager import ConversationError
from conversation_manager.native_controls import NativeControls


class NativeControlTests(unittest.IsolatedAsyncioTestCase):
    def controls(self):
        controls = NativeControls(AsyncMock(), {"userAgent": "codex_cli_rs/0.162.1"})
        controls.models = [
            {
                "id": "one",
                "model": "one",
                "displayName": "One",
                "supportedReasoningEfforts": [
                    {"reasoningEffort": "low"},
                    {"reasoningEffort": "high"},
                ],
                "defaultReasoningEffort": "low",
                "serviceTiers": [{"id": "fast", "name": "Fast"}],
            },
            {"id": "two", "model": "two", "displayName": "Two"},
        ]
        controls.capture("thread", {"model": "one", "serviceTier": None})
        return controls

    async def test_setting_slash_aliases_use_native_choices_and_read_without_dispatch(
        self,
    ):
        controls = self.controls()
        controls.set_config = AsyncMock(return_value={"options": []})
        await controls.command("thread", "reasoning", "high")
        controls.set_config.assert_awaited_with("thread", "effort", "high")
        await controls.command("thread", "fast", "on")
        controls.set_config.assert_awaited_with("thread", "serviceTier", "fast")
        before = controls.set_config.await_count
        result = await controls.command("thread", "model", "")
        self.assertEqual(result["output"]["id"], "model")
        self.assertEqual(controls.set_config.await_count, before)
        rows = {
            row["name"]: row for row in (await controls.commands("thread"))["commands"]
        }
        self.assertEqual(rows["reasoning"]["options"][1]["value"], "high")
        controls.capture("thread", {"model": "two"})
        with self.assertRaises(ConversationError):
            await controls.command("thread", "fast", "on")

    async def test_diff_reads_only_native_file_change_items(self):
        controls = self.controls()
        controls.rpc = AsyncMock(
            return_value={
                "thread": {
                    "turns": [
                        {
                            "items": [
                                {"type": "fileChange", "changes": [{"path": "one.py"}]},
                                {"type": "agentMessage", "text": "other"},
                            ]
                        }
                    ]
                }
            }
        )
        result = await controls.command("thread", "diff", "")
        self.assertEqual(
            result,
            {"output": [{"type": "fileChange", "changes": [{"path": "one.py"}]}]},
        )
        controls.rpc.assert_awaited_once_with(
            "thread/read", {"threadId": "thread", "includeTurns": True}
        )

    async def test_speed_change_requires_notification_and_keeps_model(self):
        controls = self.controls()

        async def rpc(method, params):
            self.assertEqual(method, "thread/settings/update")
            self.assertEqual(params, {"threadId": "thread", "serviceTier": "fast"})
            controls.capture("thread", {"model": "one", "serviceTier": None})
            self.assertFalse(controls.waiters["thread"]["future"].done())
            controls.capture(
                "thread", {"model": "one", "serviceTier": "fast", "effort": "high"}
            )
            return {}

        controls.rpc = AsyncMock(side_effect=rpc)
        changed = await controls.set_config("thread", "serviceTier", "fast")
        self.assertEqual(
            {row["id"]: row["currentValue"] for row in changed["options"]},
            {"model": "one", "effort": "high", "serviceTier": "fast"},
        )
        self.assertFalse(controls.waiters)

    async def test_catalog_never_offers_unadvertised_speed_and_old_versions_cannot_mutate(
        self,
    ):
        controls = self.controls()
        self.assertIn(
            "fast",
            [row["name"] for row in (await controls.commands("thread"))["commands"]],
        )
        controls.capture("thread", {"model": "two", "serviceTier": "fast"})
        fast = next(
            row
            for row in (await controls.commands("thread"))["commands"]
            if row["name"] == "fast"
        )
        self.assertEqual(fast["execution"], "unsupported")
        self.assertNotIn("terminal", fast)
        with self.assertRaises(ConversationError):
            await controls.set_config("thread", "serviceTier", "fast")
        controls.rpc.assert_not_awaited()

        controls.initialization = {"userAgent": "codex_cli_rs/0.100.0"}
        self.assertEqual(await controls.configuration("thread"), {"options": []})

    async def test_complete_inventory_has_explicit_execution_and_terminal_requirements(
        self,
    ):
        controls = self.controls()
        rows = {
            row["name"]: row for row in (await controls.commands("thread"))["commands"]
        }
        self.assertTrue(
            {
                "permissions",
                "plan",
                "goal",
                "rename",
                "skills",
                "apps",
                "hooks",
                "plugins",
                "export",
                "fork",
                "mcp",
                "usage",
                "ps",
                "stop",
            }
            <= rows.keys()
        )
        self.assertEqual(rows["plugins"]["execution"], "unsupported")
        self.assertTrue(rows["plugins"]["terminal"])
        self.assertEqual(rows["mcp"]["execution"], "native")
        controls.rpc.assert_not_awaited()

    async def test_planning_and_permissions_wait_for_effective_settings(self):
        controls = self.controls()
        controls.capture(
            "thread",
            {
                "cwd": "/workspace",
                "collaborationMode": {"mode": "default"},
                "activePermissionProfile": {"id": ":read-only"},
            },
        )
        controls.permission_profiles["/workspace"] = [
            {"id": ":read-only", "allowed": True},
            {"id": ":workspace", "allowed": True},
            {"id": ":danger", "allowed": False},
        ]

        async def rpc(method, params):
            self.assertEqual(method, "thread/settings/update")
            if "collaborationMode" in params:
                self.assertEqual(
                    params["collaborationMode"],
                    {
                        "mode": "plan",
                        "settings": {
                            "model": "one",
                            "reasoning_effort": None,
                            "developer_instructions": None,
                        },
                    },
                )
                controls.capture(
                    "thread", {"collaborationMode": params["collaborationMode"]}
                )
            else:
                self.assertEqual(
                    params, {"threadId": "thread", "permissions": ":workspace"}
                )
                controls.capture(
                    "thread", {"activePermissionProfile": {"id": ":workspace"}}
                )
            return {}

        controls.rpc = AsyncMock(side_effect=rpc)
        await controls.set_config("thread", "mode", "plan")
        await controls.set_config("thread", "permissions", ":workspace")
        with self.assertRaises(ConversationError):
            await controls.set_config("thread", "permissions", ":danger")
        self.assertEqual(controls.rpc.await_count, 2)

    async def test_direct_commands_use_scoped_rpc_and_never_send_the_command_as_a_prompt(
        self,
    ):
        controls = self.controls()
        controls.rpc = AsyncMock(return_value={})
        await controls.command("thread", "rename", "Release prep")
        controls.rpc.assert_awaited_with(
            "thread/name/set", {"threadId": "thread", "name": "Release prep"}
        )
        await controls.command("thread", "goal", "Finish migration")
        controls.rpc.assert_awaited_with(
            "thread/goal/set",
            {
                "threadId": "thread",
                "origin": "user",
                "objective": "Finish migration",
                "status": "active",
            },
        )
        await controls.command("thread", "goal", "pause")
        controls.rpc.assert_awaited_with(
            "thread/goal/set",
            {"threadId": "thread", "origin": "user", "status": "paused"},
        )
        await controls.command("thread", "stop", "")
        controls.rpc.assert_awaited_with(
            "thread/backgroundTerminals/clean", {"threadId": "thread"}
        )
        with self.assertRaises(ConversationError):
            await controls.command("thread", "logout", "")
        self.assertNotIn(
            "turn/start", [call.args[0] for call in controls.rpc.await_args_list]
        )

    async def test_legacy_sandbox_can_choose_an_allowed_named_profile(self):
        controls = self.controls()
        controls.capture(
            "thread", {"cwd": "/workspace", "activePermissionProfile": None}
        )
        controls.permission_profiles["/workspace"] = [
            {"id": ":workspace", "allowed": True},
            {"id": ":danger", "allowed": False},
        ]
        option = next(
            row
            for row in (await controls.configuration("thread"))["options"]
            if row["id"] == "permissions"
        )
        self.assertEqual(option["currentValue"], "Current policy")
        self.assertEqual(
            option["options"], [{"value": ":workspace", "name": ":workspace"}]
        )
        command = next(
            row
            for row in (await controls.commands("thread"))["commands"]
            if row["name"] == "permissions"
        )
        self.assertEqual(command["execution"], "setting")

    async def test_skill_invocation_uses_the_advertised_native_path(self):
        controls = self.controls()

        async def rpc(method, params):
            if method == "skills/list":
                return {
                    "data": [
                        {
                            "skills": [
                                {
                                    "name": "audit",
                                    "path": "/trusted/SKILL.md",
                                    "enabled": True,
                                }
                            ]
                        }
                    ]
                }
            self.assertEqual(
                params["input"],
                [
                    {"type": "skill", "name": "audit", "path": "/trusted/SKILL.md"},
                    {"type": "text", "text": "check tests"},
                ],
            )
            return {"turn": {"id": "turn"}}

        controls.rpc = AsyncMock(side_effect=rpc)
        await controls.command("thread", "skills", "audit check tests")
        with self.assertRaises(ConversationError):
            await controls.command("thread", "skills", "/untrusted/SKILL.md")

    async def test_model_change_clears_incompatible_tier_and_effort_without_leaving_plan_mode(
        self,
    ):
        controls = self.controls()
        controls.models[1].update(
            supportedReasoningEfforts=[{"reasoningEffort": "low"}],
            defaultReasoningEffort="low",
        )
        controls.capture(
            "thread",
            {
                "serviceTier": "fast",
                "effort": "high",
                "collaborationMode": {
                    "mode": "plan",
                    "settings": {
                        "model": "one",
                        "reasoning_effort": "high",
                        "developer_instructions": None,
                    },
                },
            },
        )

        async def rpc(method, params):
            self.assertEqual(
                params,
                {
                    "threadId": "thread",
                    "model": "two",
                    "serviceTier": None,
                    "effort": "low",
                    "collaborationMode": {
                        "mode": "plan",
                        "settings": {
                            "model": "two",
                            "reasoning_effort": "low",
                            "developer_instructions": None,
                        },
                    },
                },
            )
            controls.capture(
                "thread", {"model": "two", "serviceTier": "fast", "effort": "high"}
            )
            self.assertFalse(controls.waiters["thread"]["future"].done())
            controls.capture(
                "thread",
                {
                    "model": "two",
                    "serviceTier": None,
                    "effort": "low",
                    "collaborationMode": params["collaborationMode"],
                },
            )
            return {}

        controls.rpc = AsyncMock(side_effect=rpc)
        result = await controls.set_config("thread", "model", "two")
        values = {row["id"]: row["currentValue"] for row in result["options"]}
        self.assertEqual(values, {"model": "two", "effort": "low", "mode": "plan"})

    async def test_unknown_setting_or_command_never_dispatches(self):
        controls = self.controls()
        with self.assertRaises(ConversationError):
            await controls.command("thread", "arbitrary/rpc", "")
        with self.assertRaises(ConversationError):
            await controls.set_config("thread", "model", "unadvertised")
        controls.rpc.assert_not_awaited()
