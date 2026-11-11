"""Conversation-owned bootstrap and receipts with an unrelated injected process owner."""
import asyncio
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

from conversation_manager import Client, PreparedLaunch, WorkerLauncher, ConversationError


class Owner:
    def __init__(self):
        self.processes, self.commands = {}, []
        self.stop_calls = 0
        self.lose_stop = False

    async def start(self, command, *, cwd, operation_id):
        self.commands.append(list(command))
        process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        threading.Thread(target=process.wait, daemon=True).start()
        identity = uuid.uuid4().hex
        self.processes[identity] = process
        return dict(owner='test-process-owner', id=identity)

    async def inspect(self, ref):
        if ref.get('owner') != 'test-process-owner' or ref['id'] not in self.processes:
            raise ConversationError('conflict', 'Not owned by this process owner')
        return dict(runtime_ref=ref, state='running' if self.processes[ref['id']].poll() is None else 'exited')

    async def stop(self, ref, *, operation_id):
        await self.inspect(ref)
        self.stop_calls += 1
        self.processes[ref['id']].terminate()
        if self.lose_stop: raise OSError('Lost stop acknowledgement')

    def cleanup(self):
        for process in self.processes.values():
            if process.poll() is None:
                try: os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError: pass
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()


@unittest.skipIf(os.name == 'nt', 'Isolated POSIX process-owner fixture')
class WorkerLauncherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='managed-worker-test-')
        self.root = Path(self.temp.name)
        self.owner = Owner()
        self.client = Client(self.root / 'server')
        self.launcher = WorkerLauncher(self.owner, self.client.home, timeout=5, service_factory='conversation_manager.fixture_sdk:factory')

    async def asyncTearDown(self):
        await asyncio.to_thread(self.owner.cleanup)
        self.temp.cleanup()

    def prepared(self, **options):
        return PreparedLaunch(backend_id='claude', adapter_id='claude-sdk', protocol='claude-agent-sdk', cwd=str(self.root),
            argv=[], transport={'kind': 'sdk'}, **options)

    async def test_bootstrap_is_owned_by_server_and_secrets_never_reach_process_arguments_or_store(self):
        checkpoints = []
        async def connection(ref):
            checkpoints.append(ref)
            self.assertEqual(self.client.store.get('connection', ref['connection_id'])['connection_ref'], ref)
        self.launcher.on_connection = connection
        launched = await self.client.launch(self.prepared(env={'WORKER_TEST_SECRET': 'private-test-token'}),
            launcher=self.launcher, operation_id='worker-start')
        self.assertEqual(launched['status'], 'completed', launched)
        self.assertEqual(len(self.owner.commands), 1)
        self.assertIn('conversation_manager.worker', self.owner.commands[0])
        self.assertNotIn('private-test-token', str(self.owner.commands))
        self.assertNotIn(b'private-test-token', self.client.store.path.read_bytes())
        self.assertEqual(checkpoints[0], launched['result']['connection_ref'])
        connection = await self.client.connect(checkpoints[0])
        created = await connection.create_session({}, operation_id='create-fixture')
        self.assertEqual(created['status'], 'completed', created)
        await connection.disconnect()
        again = await self.client.launch(self.prepared(env={'WORKER_TEST_SECRET': 'private-test-token'}),
            launcher=self.launcher, operation_id='worker-start')
        self.assertEqual(again, launched)
        self.assertEqual(len(self.owner.commands), 1)

    async def test_checkpoint_failure_retains_exact_runtime_and_never_dispatches_another_worker(self):
        async def broken(_): raise OSError('Lost integrating-application checkpoint')
        self.launcher.on_connection = broken
        launched = await self.client.launch(self.prepared(), launcher=self.launcher, operation_id='checkpoint-lost')
        self.assertEqual(launched['status'], 'uncertain')
        self.assertIsNotNone(launched['recovery']['runtime_ref'])
        self.assertIsNotNone(launched['recovery']['connection_ref'])
        self.assertEqual(await self.client.launch(self.prepared(), launcher=self.launcher, operation_id='checkpoint-lost'), launched)
        self.assertEqual(len(self.owner.commands), 1)

    async def test_termination_retries_observe_without_resending_a_lost_stop(self):
        launched = await self.client.launch(self.prepared(), launcher=self.launcher, operation_id='launch-stop-test')
        ref = launched['recovery']['runtime_ref']
        self.owner.lose_stop = True
        first = await self.launcher.terminate(ref, operation_id='stop-lost')
        self.assertEqual(first['status'], 'uncertain')
        async with asyncio.timeout(5):
            while (await self.owner.inspect(ref))['state'] == 'running': await asyncio.sleep(.02)
        second = await self.launcher.terminate(ref, operation_id='stop-lost')
        self.assertEqual(second['status'], 'completed')
        self.assertEqual(self.owner.stop_calls, 1)

    async def test_long_socket_root_fails_before_process_dispatch(self):
        folder = self.root / ('x' * 110)
        folder.mkdir()
        with patch('conversation_manager.managed_launcher.tempfile.mkdtemp', return_value=str(folder)):
            result = await self.client.launch(self.prepared(), launcher=self.launcher, operation_id='too-long')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.owner.commands, [])
