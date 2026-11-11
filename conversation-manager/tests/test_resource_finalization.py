"""Only captured optional resources can be omitted before the first session."""
import asyncio
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock
from conversation_manager import PreparedLaunch
from conversation_manager.core import Core
from conversation_manager.store import Store


class FinalizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(tmp.name)
        self.ref = dict(schema="conversation-manager/connection-ref-1", connection_id="fixture", backend_id="fixture", adapter_id="fixture", runtime_ref={"owner": "fixture", "id": "r"})
        self.prepared = PreparedLaunch(backend_id="fixture", adapter_id="fixture", argv=["fixture"], cwd=tmp.name,
            resource_snapshot_ref="sha256:" + "a" * 64,
            settings={"cwd": tmp.name, "model": "fixed", "instructions": {"text": "captured rules"}},
            resource_policy={"schema": "conversation-manager/resource-policy-1", "items": [{"id": "prompt", "kind": "instructions", "required": False, "key": None}]})
        self.core = Core(self.store, self.prepared, self.ref)
        self.core.adapter = Mock(protocol_version=1, operations={"create_session": {"supported": True}, "instructions_delivery": {"supported": False}},
            create=AsyncMock(return_value=("native-fixture", {})))

    async def finalize(self, selected, operation_id="finalize"):
        return await self.core.dispatch("finalize_resources", {"selected": selected, "resource_snapshot_ref": "sha256:" + "b" * 64, "operation_id": operation_id})

    async def test_once_only_finalize_then_immutable_session(self):
        early = await self.core.dispatch("create_session", {"settings": {}, "operation_id": "early"})
        self.assertEqual(early["status"], "failed")
        checked = await self.core.dispatch("assess_resources", {})
        self.assertFalse(checked["items"][0]["supported"])
        result = await self.finalize([])
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result, await self.finalize([]))
        self.assertEqual(result["result"]["settings"], {"cwd": self.prepared.cwd, "model": "fixed"})
        again = await self.finalize([], "another-finalization")
        self.assertEqual(again["error"]["code"], "conflict")
        changed = await self.core.dispatch("create_session", {"settings": {"model": "different"}, "operation_id": "changed"})
        self.assertEqual(changed["error"]["code"], "conflict")
        created = await self.core.dispatch("create_session", {"settings": {}, "operation_id": "create"})
        self.assertEqual(created["status"], "completed", created)
        self.assertEqual(created["result"]["session_ref"]["resource_snapshot_ref"], "sha256:" + "b" * 64)
        self.assertEqual(self.core.adapter.create.await_count, 1)

    async def test_required_cannot_be_dropped_and_unknown_selection_cannot_be_added(self):
        self.prepared.resource_policy["items"][0]["required"] = True
        self.assertEqual((await self.finalize([]))["error"]["code"], "conflict")
        self.assertEqual((await self.finalize(["invented"], "new"))["error"]["code"], "validation")
        self.assertEqual((await self.finalize(["prompt"], "required"))["status"], "unsupported")
        self.core.adapter.create.assert_not_awaited()

    async def test_concurrent_handles_cannot_rebind(self):
        results = await asyncio.gather(self.finalize([], "one"), self.finalize([], "two"))
        self.assertEqual(sorted(r["status"] for r in results), ["completed", "failed"])

    async def test_legacy_descriptors_remain_strict_and_need_no_finalization(self):
        self.prepared.resource_policy = None
        created = await self.core.dispatch("create_session", {"settings": {}, "operation_id": "legacy"})
        self.assertEqual(created["status"], "completed")
