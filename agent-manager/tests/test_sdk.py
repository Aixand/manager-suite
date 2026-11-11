import asyncio
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tests.support import ManagedFixture
from agent_manager import Client, Error
from agent_manager.runtime.runs import finish_run, read_run, update_run
from agent_manager.runtime.supervisor import supervise




class ManagedSDKTests(ManagedFixture, unittest.IsolatedAsyncioTestCase):



    async def test_profiles_expose_native_identity_without_saved_auth_fields(self):
        self.create()
        rows = await self.client().profiles()
        self.assertEqual([row['profile'] for row in rows], ['work'])
        self.assertEqual(rows[0]['home'], str(self.manager.home('work')))
        self.assertNotIn('auth', rows[0])
        self.assertNotIn('weekly_left', rows[0])



    async def test_runs_include_pending_and_history_without_private_launch_data(self):
        self.create()
        first = await self.launch(env={"SECRET": "private-launch-token"})
        second = await self.launch()
        update_run(self.manager, second.run_id, status="uncertain", session_id=None)
        finish_run(self.manager, first.run_id, status="finished")
        rows = await self.client().runs()
        self.assertEqual(rows[0]["run_id"], second.run_id)
        self.assertTrue(rows[0]["active"])
        self.assertIsNone(rows[0]["session_id"])
        self.assertEqual(rows[1]["status"], "finished")
        self.assertFalse(rows[1]["active"])
        public = json.dumps(rows)
        for private in ("private-launch-token", "snapshot", "worker_pid", "connection", '"launch"', '"home"'):
            self.assertNotIn(private, public)
        self.assertEqual(len(await self.client().runs(include_finished=False)), 1)
        self.assertEqual(len(await self.client().runs(limit=1)), 1)
        for limit in (True, 0, 1001):
            with self.assertRaises(ValueError): await self.client().runs(limit=limit)

    async def test_launch_is_prepared_for_undercurrent_without_invoking_provider(self):
        self.create()
        with patch.object(self.manager, "_invoke_native", side_effect=AssertionError("must not spawn provider directly")):
            agent = await self.launch(args=["--extra", "value with spaces"], env={"TASK": "research"})
        record = read_run(self.manager, agent.run_id)
        self.assertEqual(record["launch"]["command"], [str(self.fake), "--configured", "--extra", "value with spaces"])
        self.assertEqual(record["launch"]["env"]["TEST_AGENT_HOME"], agent.home)
        self.assertEqual(record["launch"]["env"]["TASK"], "research")
        self.assertEqual(agent.profile, "work")
        self.assertTrue(callable(agent.wait))
        self.assertFalse((self.manager.home("work") / "observed.json").exists())

    async def test_sdk_launches_without_any_resource_manager(self):
        self.create()
        with patch.dict(sys.modules, {name: None for name in ("mcp_manager", "prompt_manager", "skill_manager")}):
            for options in ({}, {"args": ["--yolo"]}):
                agent = await self.launch(**options)
                record = read_run(self.manager, agent.run_id)
                self.assertNotIn("mcp", record)
                self.assertEqual(record["status"], "running")

    async def test_durable_lease_survives_worker_exit_and_profile_rename(self):
        self.create()
        agent = await self.launch()
        home = self.manager.home('work')
        identity = self.manager.state()['profiles']['work']['id']
        with patch('os.kill', side_effect=ProcessLookupError):
            self.assertEqual(len(self.manager.active_runs(self.manager.state(), 'work')), 1)
        self.manager.rename('work', 'renamed')
        self.assertEqual(self.manager.home('renamed'), home)
        self.assertEqual(self.manager.state()['runs'][agent.run_id]['profile_id'], identity)
        with self.assertRaisesRegex(Error, 'Stop this profile'):
            with patch('agent_manager.runtime.lifecycle.reconcile'):
                await asyncio.to_thread(self.manager.login, 'renamed')
        self.assertEqual((await self.client().get(agent.run_id)).session_id, agent.session_id)

    async def test_launch_and_finalization_preserve_native_profile_files_in_place(self):
        self.create()
        first = await self.launch()
        marker = self.manager.home('work') / 'native-login-marker'
        marker.write_text('provider-owned-live-update')
        second = await self.launch()
        finish_run(self.manager, first.run_id, status='finished')
        self.assertEqual(marker.read_text(), 'provider-owned-live-update')
        self.assertIn(second.run_id, self.manager.state()['runs'])



    async def test_cancelled_caller_leaves_recoverable_launch(self):
        self.create()
        with patch("agent_manager.interfaces.sdk.launch_worker") as launch:
            task = asyncio.create_task(self.client().spawn(profile="work"))
            while not launch.called: await asyncio.sleep(.001)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(len(self.manager.state()["runs"]), 1)
        self.assertEqual(next(iter(self.manager.state()["runs"].values()))["status"], "prepared")

    async def test_reconcile_restarts_only_managed_workers(self):
        self.create()
        agent = await self.launch()
        update_run(self.manager, agent.run_id, status='prepared', session_id=None)
        state = self.manager.state()
        state["runs"]["legacy"] = {"pid": os.getpid(), "agent": "work", "environment_id": self.manager.resolve_profile("work").environment_id}
        self.manager.save(state)
        with patch("agent_manager.runtime.runs.launch_worker") as worker:
            self.assertEqual(await self.client().reconcile(), [agent.run_id])
        self.assertEqual(worker.call_count, 1)

    def runtime_module(self, *, matches=(), error=None):
        class RuntimeError(Exception):
            effect_certainty = None
            category = "Unavailable"

        class Runtime:
            async def __aenter__(self): return self
            async def __aexit__(self, *_): pass

        runtime = Runtime()
        runtime.list = AsyncMock(return_value=list(matches))
        runtime.start = AsyncMock(side_effect=error)
        runtime.wait = AsyncMock()
        return runtime, SimpleNamespace(Client=lambda **_: runtime, UndercurrentError=RuntimeError), RuntimeError

    async def test_uncertain_launch_is_retained_and_never_blindly_replayed(self):
        self.create()
        agent = await self.launch()
        update_run(self.manager, agent.run_id, session_id=None, status="prepared")
        runtime, module, error_type = self.runtime_module()
        runtime.start.side_effect = error_type("response lost")
        with patch.dict(sys.modules, undercurrent=module):
            with self.assertRaises(error_type): await supervise(self.manager, agent.run_id)
            await supervise(self.manager, agent.run_id)
        self.assertEqual(runtime.start.call_count, 1)
        self.assertEqual(read_run(self.manager, agent.run_id)["status"], "uncertain")
        self.assertIn(agent.run_id, self.manager.state()["runs"])

    async def test_recovery_adopts_session_by_label_after_lost_start_response(self):
        self.create()
        agent = await self.launch()
        update_run(self.manager, agent.run_id, session_id=None, status="submitting")
        runtime, module, _ = self.runtime_module(matches=[SimpleNamespace(id="recovered-id")])
        with patch.dict(sys.modules, undercurrent=module): await supervise(self.manager, agent.run_id)
        runtime.start.assert_not_called()
        runtime.wait.assert_awaited_once_with("recovered-id", lifecycle=("stopped", "exited", "failed"), timeout=300)
        self.assertEqual(read_run(self.manager, agent.run_id)["session_id"], "recovered-id")
        self.assertNotIn(agent.run_id, self.manager.state()["runs"])

    async def test_confirmed_absent_launch_releases_lease(self):
        self.create()
        agent = await self.launch()
        update_run(self.manager, agent.run_id, session_id=None, status="prepared")
        runtime, module, error_type = self.runtime_module()
        error = error_type("rejected")
        error.effect_certainty = "known_absent"
        runtime.start.side_effect = error
        with patch.dict(sys.modules, undercurrent=module): await supervise(self.manager, agent.run_id)
        self.assertNotIn(agent.run_id, self.manager.state()["runs"])
        self.assertEqual(read_run(self.manager, agent.run_id)["status"], "failed")

    async def test_ambiguous_runtime_recovery_retains_lease_without_adopting_or_replaying(self):
        self.create()
        agent = await self.launch()
        update_run(self.manager, agent.run_id, session_id=None, status='submitting')
        runtime, module, _ = self.runtime_module(matches=[SimpleNamespace(id='one'), SimpleNamespace(id='two')])
        with patch.dict(sys.modules, undercurrent=module):
            await supervise(self.manager, agent.run_id)
        runtime.start.assert_not_called()
        runtime.wait.assert_not_called()
        row = read_run(self.manager, agent.run_id)
        self.assertEqual(row['status'], 'uncertain')
        self.assertIsNone(row['session_id'])
        self.assertIn(agent.run_id, self.manager.state()['runs'])

    async def test_failed_context_entry_closes_only_owned_runtime_connections(self):
        self.create()
        for borrowed in (False, True):
            for failure in ('connect', 'reconcile'):
                with self.subTest(borrowed=borrowed, failure=failure):
                    runtime = SimpleNamespace(connect=AsyncMock(), close=AsyncMock())
                    module = SimpleNamespace(Client=lambda: runtime)
                    with patch.dict(sys.modules, undercurrent=module):
                        client = Client('test', config=self.config, root=self.base / 'homes',
                                        runtime=runtime if borrowed else None)
                    client.reconcile = AsyncMock()
                    getattr(runtime if failure == 'connect' else client, failure).side_effect = OSError('initialization failed')
                    with self.assertRaisesRegex(OSError, 'initialization failed'):
                        await client.__aenter__()
                    if borrowed: runtime.close.assert_not_called()
                    else: runtime.close.assert_awaited_once()
