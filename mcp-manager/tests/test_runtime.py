import asyncio
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from mcp_manager import Manager, McpError, digest
from fixtures.backend import Connector


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mcp-runtime-")
        self.addCleanup(self.temp.cleanup)
        self.manager = Manager(Path(self.temp.name) / "store")
        self.target = {"schema": "mcp-manager/runtime-target-1", "connector_id": "fixture", "backend_id": "fixture-backend",
            "connection_ref": {"connection_id": "retained-connection", "runtime_ref": {"owner": "fixture", "id": "runtime-1"}},
            "runtime_ref": {"owner": "fixture", "id": "runtime-1"}, "native_session_id": "session-1",
            "selection_ref": {"snapshot": "original"}, "selection_digest": digest({"servers": ["docs", "sonar"]}),
            "prepared_generations": [{"name": "sonar", "generation": "saved-generation"}]}

    async def reconnect(self, connector, operation_id="operation-1", **kwargs):
        return await self.manager.reconnect(connector, self.target, scope=kwargs.pop("scope", "session"),
            operation_id=operation_id, expected_selection_digest=self.target["selection_digest"], **kwargs)

    def dispatches(self, connector):
        return [row for row in connector.calls if row[0] == "reconnect"]

    async def test_inventory_normalizes_unknown_and_preserves_observed_failed_state(self):
        connector = Connector()
        before = copy.deepcopy(self.target)
        caps = await self.manager.runtime_capabilities(connector, self.target)
        self.assertEqual(caps["evidence"]["adapter_version"], "fixture-1")
        result = await self.manager.runtime_status(connector, self.target)
        self.assertEqual([s["connection_status"] for s in result["servers"]], ["failed", "unknown"])
        self.assertEqual(result["servers"][1]["definition_state"], "unknown")
        filtered = await self.manager.runtime_status(connector, self.target, scope="server", server_name="docs")
        self.assertEqual(len(filtered["servers"]), 1)
        self.assertEqual(self.target, before)
        self.assertFalse(self.manager.home.exists())

    async def test_completed_reconnect_preserves_selection_but_does_not_claim_connection_success(self):
        connector = Connector()
        result = await self.reconnect(connector)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["selection_preserved"])
        self.assertEqual(result["inventory"]["servers"][0]["connection_status"], "failed")
        self.assertEqual(result["selection_after"]["prepared_generations"], self.target["prepared_generations"])
        self.assertEqual(self.manager.runtime_operation("operation-1"), result)
        self.assertEqual(len(self.dispatches(connector)), 1)
        self.assertFalse((self.manager.home / "projects").exists())
        self.assertFalse((self.manager.home / "registry.json").exists())

    async def test_acknowledgment_is_accepted_with_unknown_observation(self):
        result = await self.reconnect(Connector({"status": "accepted"}))
        self.assertEqual(result["status"], "accepted")
        self.assertIsNone(result["selection_preserved"])
        self.assertIsNone(result["inventory"])

    async def test_process_scope_reports_other_affected_targets_and_filter_does_not_reduce_scope(self):
        result = await self.reconnect(Connector(), scope="process", server_name="docs")
        self.assertEqual(result["actual_scope"], "process")
        self.assertEqual(len(result["affected_targets"]), 2)
        self.assertEqual([s["name"] for s in result["inventory"]["servers"]], ["docs"])

    async def test_unsupported_scope_filter_or_preservation_never_dispatch(self):
        for options in ({"scopes": ["process"]}, {"preserve_selection": False}, {"reconnect": False},
                        {"inventory": False}, {"server_filter": False}):
            with self.subTest(options=options):
                connector = Connector(options)
                result = await self.reconnect(connector, server_name="docs")
                self.assertEqual(result["status"], "unsupported")
                self.assertEqual(result["error"]["code"], "unsupported")
                self.assertEqual(self.dispatches(connector), [])
        self.assertFalse(self.manager.home.exists())

    async def test_missing_capability_is_unsupported(self):
        class Empty(Connector):
            async def capabilities(self, target):
                return {"schema": "mcp-manager/runtime-capabilities-1", "connector_id": target["connector_id"], "backend_id": target["backend_id"]}
        caps = await self.manager.runtime_capabilities(Empty(), self.target)
        self.assertFalse(caps["inventory"])
        self.assertEqual(caps["reconnect_scopes"], [])
        with self.assertRaises(McpError) as caught:
            await self.manager.runtime_status(Empty(), self.target)
        self.assertEqual(caught.exception.code, "unsupported")

    async def test_stale_selection_and_generation_are_conflicts_before_dispatch(self):
        for options in ({"selection_digest": "sha256:stale"}, {"generations": []}):
            connector = Connector(options)
            with self.assertRaises(McpError) as caught:
                await self.reconnect(connector)
            self.assertEqual(caught.exception.code, "conflict")
            self.assertEqual(self.dispatches(connector), [])
        with self.assertRaises(McpError):
            await self.manager.reconnect(Connector(), self.target, scope="session", operation_id="missing-digest")
        self.assertFalse(self.manager.home.exists())

    async def test_reused_key_returns_retained_receipt_without_backend_and_changed_requests_conflict(self):
        result = await self.reconnect(Connector())
        self.assertEqual(await self.reconnect(object()), result)
        for kwargs in ({"scope": "process"}, {"server_name": "docs"}):
            with self.assertRaises(McpError) as caught:
                await self.reconnect(object(), **kwargs)
            self.assertEqual(caught.exception.code, "conflict")
        original = copy.deepcopy(self.target)
        self.target["prepared_generations"] = []
        with self.assertRaises(McpError):
            await self.reconnect(object())
        self.target = original
        self.target["connection_ref"]["observed_at"] = "new observation"
        self.assertEqual(await self.reconnect(object()), result)

    async def test_lost_reply_survives_new_manager_and_does_not_replay(self):
        connector = Connector({"lost_reply": True})
        result = await self.reconnect(connector)
        self.assertEqual(result["status"], "uncertain")
        self.assertTrue(result["error"]["uncertain"])
        self.assertEqual(result["recovery"]["target"], self.target)
        self.manager = Manager(self.manager.home)
        self.assertEqual(await self.reconnect(object()), result)
        self.assertEqual(len(self.dispatches(connector)), 1)

    async def test_cancelled_client_and_concurrent_same_key_keep_one_dispatch(self):
        connector = Connector({"block": True})
        task = asyncio.create_task(self.reconnect(connector))
        await asyncio.wait_for(connector.dispatched.wait(), 2)
        second = await self.reconnect(object())
        self.assertEqual(second["status"], "uncertain")
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(await self.reconnect(object()), second)
        self.assertEqual(len(self.dispatches(connector)), 1)

    async def test_widened_scope_or_changed_generation_never_claim_success(self):
        widened = await self.reconnect(Connector({"actual_scope": "process"}), operation_id="widened")
        self.assertEqual(widened["status"], "uncertain")
        self.assertEqual(widened["actual_scope"], "process")
        changed = await self.reconnect(Connector({"change_generation": True}), operation_id="changed")
        self.assertEqual(changed["status"], "failed")
        self.assertFalse(changed["selection_preserved"])
        self.assertEqual(changed["error"]["code"], "integrity")

    async def test_target_and_scope_validation(self):
        for target, scope, server in (({}, "session", None), (self.target, "invalid", None),
                                     (self.target, "server", None), ({**self.target, "native_session_id": None}, "session", None)):
            with self.assertRaises(McpError) as caught:
                await self.manager.runtime_status(Connector(), target, scope=scope, server_name=server)
            self.assertEqual(caught.exception.code, "validation")
        with self.assertRaises(McpError) as caught:
            await self.manager.runtime_status(Connector({"inventory_scope": "process"}), self.target)
        self.assertEqual(caught.exception.code, "integrity")
        for key in ("", "x" * 129, 3):
            with self.assertRaises(McpError):
                await self.reconnect(Connector(), operation_id=key)
        with self.assertRaises(McpError) as caught:
            self.manager.runtime_operation("missing")
        self.assertEqual(caught.exception.code, "not_found")

    async def test_structural_error_translates_without_backend_import(self):
        class BackendError(Exception):
            code = "unsupported"
            message = "Adapter lacks inventory"
            uncertain = False
        class Backend(Connector):
            async def inventory(self, target, **kwargs):
                raise BackendError()
        with self.assertRaises(McpError) as caught:
            await self.manager.runtime_status(Backend(), self.target)
        self.assertEqual(caught.exception.code, "unsupported")
        with self.assertRaises(McpError) as caught:
            await self.reconnect(Backend())
        self.assertEqual(caught.exception.operation_id, "operation-1")
        self.assertEqual(caught.exception.recovery["target"], self.target)

    async def test_corrupt_retained_record_is_an_integrity_error_and_never_dispatches(self):
        await self.reconnect(Connector())
        path = next((self.manager.home / "runtime-operations").glob("*.json"))
        path.write_text("[]")
        with self.assertRaises(McpError) as caught:
            self.manager.runtime_operation("operation-1")
        self.assertEqual(caught.exception.code, "integrity")
        with self.assertRaises(McpError) as caught:
            await self.reconnect(object())
        self.assertEqual(caught.exception.code, "integrity")
        self.assertEqual(caught.exception.operation_id, "operation-1")

    async def test_failed_intent_storage_does_not_dispatch_and_lost_result_storage_is_uncertain(self):
        connector = Connector()
        with patch("mcp_manager.runtime.write", side_effect=OSError("unavailable")):
            with self.assertRaises(McpError) as caught:
                await self.reconnect(connector)
        self.assertEqual(caught.exception.code, "io")
        self.assertEqual(self.dispatches(connector), [])
        from mcp_manager.runtime import write
        count = 0
        def fail_final(*args):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("lost result")
            return write(*args)
        with patch("mcp_manager.runtime.write", side_effect=fail_final):
            result = await self.reconnect(connector)
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(len(self.dispatches(connector)), 1)
        self.assertEqual((await self.reconnect(object()))["status"], "uncertain")


if __name__ == "__main__":
    unittest.main()
