import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from conversation_manager import ConversationError
from conversation_manager.adapters.opencode import OpenCodeAdapter
from conversation_manager.slash_commands import catalog


class SlashCatalogTests(unittest.TestCase):
    def test_actionable_settings_are_distinct_from_executable_commands(self):
        value = {
            "state": "ready",
            "source": "fixture",
            "commands": [
                {"name": "review", "execution": "native", "inputHint": "instructions"},
                {
                    "name": "model",
                    "execution": "setting",
                    "configId": "model",
                    "terminal": True,
                    "reason": "Old inventory reason",
                },
                {
                    "name": "hooks",
                    "execution": "unsupported",
                    "terminal": True,
                    "reason": "Terminal only",
                },
            ],
        }
        result = catalog(value, backend_id="codex", available_only=True)
        rows = {row["name"]: row for row in result["commands"]}
        self.assertEqual(set(rows), {"review", "model"})
        self.assertTrue(rows["review"]["executable"])
        self.assertEqual(rows["review"]["argument_hint"], "instructions")
        self.assertEqual(rows["review"]["slash"], "/review")
        self.assertFalse(rows["model"]["executable"])
        self.assertEqual(rows["model"]["setting_id"], "model")
        self.assertIsNone(rows["model"]["reason"])
        self.assertFalse(rows["model"]["terminal_required"])
        all_rows = catalog(value, backend_id="codex")["commands"]
        self.assertFalse(all_rows[-1]["available"])
        self.assertEqual(all_rows[-1]["reason"], "Terminal only")

    def test_loading_catalog_and_unknown_execution_do_not_enable_commands(self):
        value = {
            "state": "loading",
            "commands": [{"name": "review", "execution": "native"}],
        }
        self.assertEqual(
            catalog(value, backend_id="fixture", available_only=True)["commands"], []
        )
        value = {
            "state": "ready",
            "commands": [{"name": "team/review", "execution": "unknown"}],
        }
        result = catalog(value, backend_id="fixture")["commands"][0]
        self.assertEqual(result["slash"], "/team/review")
        self.assertFalse(result["available"])
        self.assertFalse(result["executable"])


class OpenCodeCommandDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def adapter(self):
        prepared = SimpleNamespace()
        adapter = OpenCodeAdapter(SimpleNamespace(), prepared)
        adapter.adapter_version = "2.2.5"
        adapter.configuration = AsyncMock(return_value={"options": []})
        adapter.sessions = {
            "first": {"cwd": "/project one"},
            "second": {"cwd": "/project-two"},
        }
        return adapter

    async def test_live_callbacks_are_scoped_refreshed_and_unknown_callbacks_remain_disabled(
        self,
    ):
        adapter = self.adapter()
        adapter.http = AsyncMock(
            side_effect=[
                {"data": []},
                {"data": [{"name": "review", "description": "Review this project"}]},
                {"data": []},
                {"data": [{"name": "team/audit"}]},
            ]
        )
        first = await adapter.commands({"native_session_id": "first"})
        second = await adapter.commands({"native_session_id": "second"})
        self.assertEqual(first["commands"][0]["name"], "review")
        self.assertEqual(second["commands"][0]["name"], "team/audit")
        self.assertEqual(
            adapter.http.call_args_list[1].args,
            ("GET", "/api/command?location%5Bdirectory%5D=%2Fproject+one"),
        )
        self.assertEqual(
            adapter.http.call_args_list[3].args,
            ("GET", "/api/command?location%5Bdirectory%5D=%2Fproject-two"),
        )
        result = catalog(first, backend_id="opencode")
        self.assertEqual(result["scope"], "registered-native-callbacks")
        self.assertTrue(result["commands"][0]["executable"])
        self.assertFalse(result["complete"])

    async def test_unqualified_version_does_not_enable_review(self):
        adapter = self.adapter()
        adapter.adapter_version = "2.0.18"
        adapter.http = AsyncMock(return_value={"data": [{"name": "review"}]})
        self.assertEqual(
            (await adapter.commands({"native_session_id": "first"}))["commands"][0][
                "execution"
            ],
            "unsupported",
        )

    async def test_unsupported_endpoint_and_malformed_data_remain_explicit(self):
        adapter = self.adapter()
        adapter.http = AsyncMock(
            side_effect=ConversationError("not_found", "No endpoint")
        )
        self.assertEqual(
            (await adapter.commands({"native_session_id": "first"}))["state"],
            "unsupported",
        )
        adapter.http = AsyncMock(return_value={"data": "invalid"})
        with self.assertRaises(ConversationError):
            await adapter.commands({"native_session_id": "first"})
        adapter.http = AsyncMock(
            side_effect=ConversationError("transport", "Disconnected")
        )
        with self.assertRaises(ConversationError):
            await adapter.commands({"native_session_id": "first"})
