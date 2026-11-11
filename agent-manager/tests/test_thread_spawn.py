import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import uuid

from agent_manager import Client, Error, Manager
from agent_manager.runtime.runs import finish_run, read_run, update_run


class ThreadSpawnTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.program = self.root / "codex"
        self.program.write_text("#!/bin/sh\nexit 0\n")
        self.program.chmod(0o700)
        self.config = self.root / "providers.toml"
        self.config.write_text('[providers.codex]\nexecutable=' + json.dumps(str(self.program)) +
                               '\nnative_home=' + json.dumps(str(self.root / "native")) + '\nlaunch_args=["--yolo"]\n')
        self.manager = Manager("codex", config=self.config, root=self.root / "manager")
        self.manager.new_profile("work")
        self.thread_id = str(uuid.uuid4())
        self.runtime = SimpleNamespace(connection_options={"home": str(self.root / "runtime")}, connect=AsyncMock(),
            inspect=AsyncMock(return_value=SimpleNamespace(lifecycle="running")))
        self.client = Client("codex", config=self.config, root=self.root / "manager", runtime=self.runtime)
        self.client.reconcile = AsyncMock()

    def saved_thread(self):
        directory = self.manager.home("work") / "sessions"
        directory.mkdir()
        (directory / "rollout.jsonl").write_text(json.dumps({"type": "session_meta", "payload":
            {"id": self.thread_id, "cwd": str(self.root)}}) + "\n")

    async def spawn(self, **options):
        def accepted(manager, run_id):
            update_run(manager, run_id, session_id="session-" + run_id, status="running")
        with patch("agent_manager.interfaces.sdk.launch_worker", side_effect=accepted):
            return await self.client.spawn(profile="work", **options)




    async def test_saved_thread_uses_original_directory_and_records_identity(self):
        self.saved_thread()
        terminal = await self.spawn(thread_id=self.thread_id)
        record = read_run(self.manager, terminal.run_id)
        self.assertEqual(record["launch"]["cwd"], str(self.root))
        self.assertEqual(record["launch"]["command"][-2:], ["resume", self.thread_id])
        self.assertEqual(terminal.thread_id, self.thread_id)
        self.assertEqual(terminal.launch_kind, "resume")
        self.assertEqual((await self.client.runs())[0]["thread_id"], self.thread_id)


    async def test_key_survives_finalization_and_replays_without_thread_preflight(self):
        self.saved_thread()
        key = str(uuid.uuid4())
        first = await self.spawn(thread_id=self.thread_id, idempotency_key=key)
        finish_run(self.manager, first.run_id, status="finished")
        with patch.object(self.client, "_thread_target", side_effect=AssertionError("must recover before preflight")):
            second = await self.spawn(thread_id=self.thread_id, idempotency_key=key)
        self.assertEqual(first, second)
        self.assertEqual(first.run_id, uuid.UUID(key).hex)
        with self.assertRaisesRegex(Error, "different launch arguments"):
            await self.spawn(thread_id=self.thread_id, idempotency_key=key, model="another")

    async def test_concurrent_same_key_prepares_only_one_run(self):
        import asyncio
        self.saved_thread()
        key = str(uuid.uuid4())
        def accepted(manager, run_id):
            update_run(manager, run_id, session_id="session-" + run_id, status="running")
        with patch("agent_manager.interfaces.sdk.launch_worker", side_effect=accepted) as launch:
            first, second = await asyncio.gather(self.client.spawn(profile="work", thread_id=self.thread_id, idempotency_key=key),
                                                self.client.spawn(profile="work", thread_id=self.thread_id, idempotency_key=key))
        launch.assert_called_once()
        self.assertEqual(first, second)
        self.assertEqual(len(self.manager.state()["runs"]), 1)

    async def test_invalid_targets_cannot_prepare_a_launch(self):
        self.saved_thread()
        for options in ({"thread_id": "invalid"}, {"thread_id": self.thread_id, "cwd": self.root / "elsewhere"},
                        {"thread_id": str(uuid.uuid4())}, {"thread_id": self.thread_id, "args": ["--last"]},
                        {"idempotency_key": "bad"}):
            with self.subTest(options=options), self.assertRaises((Error, ValueError)):
                await self.spawn(**options)
        with self.assertRaisesRegex(Error, "existing profile"):
            await self.client.spawn(profile=None, thread_id=self.thread_id)
        with patch.object(self.client.manager, "provider", "opencode"), self.assertRaisesRegex(Error, "Codex terminal mode"):
            await self.client.spawn(profile="work", thread_id=self.thread_id)
        self.assertEqual(self.manager.state().get("runs", {}), {})



if __name__ == "__main__":
    unittest.main()
