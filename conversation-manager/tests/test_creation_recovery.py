"""Known native identities survive failures after dispatch; no second create."""
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from conversation_manager import Client, PreparedLaunch
from conversation_manager.core import Core
from conversation_manager.store import Store


class CreationRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_and_receipt_storage_failures_keep_every_learned_identity(self):
        for failure in ("session", "completed-receipt", "all-receipts", "checkpoint", "session-and-receipts"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory(prefix="known-id-") as home:
                store = Store(home)
                ref = dict(schema="conversation-manager/connection-ref-1", connection_id="c", backend_id="codex",
                    adapter_id="native-codex", protocol="codex-app-server", runtime_ref={"owner": "fixture", "id": "r"}, transport={"kind": "fixture"})
                core = Core(store, PreparedLaunch(backend_id="codex", adapter_id="native-codex",
                    protocol="codex-app-server", argv=[], cwd=home), ref)
                core.adapter = Mock(protocol_version="codex-app-server", operations={"create_session": {"supported": True}},
                    create=AsyncMock(return_value=("known-native-id", {"thread": {"id": "known-native-id"}})))
                class Handle:
                    async def call(self, method, params): return await core.dispatch(method, params)
                    async def close(self): pass
                class Connector:
                    async def attach(self, ref): return Handle()
                client = Client(home)
                connection = await client.connect(ref, connector=Connector())
                put, finish = store.put, store.finish
                def failing_put(kind, identifier, body):
                    if kind == "session" and failure in ("session", "session-and-receipts") or kind == "operation-recovery" and failure == "checkpoint":
                        raise OSError("fixture storage failure")
                    return put(kind, identifier, body)
                def failing_finish(row):
                    if failure in ("all-receipts", "session-and-receipts") or failure == "completed-receipt" and row["status"] == "completed":
                        raise OSError("fixture receipt failure")
                    return finish(row)
                with patch.object(store, "put", side_effect=failing_put), patch.object(store, "finish", side_effect=failing_finish):
                    result = await connection.create_session({}, operation_id="known-create")
                    self.assertEqual(result["status"], "uncertain", result)
                    session = result["result"]["session_ref"]
                    for observed in (result, await client.operation("known-create"),
                                     await connection.create_session({}, operation_id="known-create")):
                        self.assertEqual(observed["result"]["session_ref"], session)
                        self.assertEqual(observed["recovery"]["session_ref"], session)
                        self.assertEqual(observed["recovery"]["native_session_id"], "known-native-id")
                        self.assertEqual(observed["recovery"]["connection_ref"], ref)
                        self.assertEqual(observed["error"]["recovery"]["session_ref"], session)
                    core.adapter.create.assert_awaited_once()
                # Independent client inspection remains possible after detaching.
                await connection.disconnect()
                self.assertEqual((await Client(home).operation("known-create"))["result"]["session_ref"], session)
