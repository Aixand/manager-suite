"""Public saved-session inspection prepares restoration without exposing storage."""
import asyncio
from pathlib import Path
import sys
import tempfile
import unittest

from conversation_manager import Client, PreparedLaunch, StandaloneLauncher, ConversationError
from conversation_manager.models import receipt


class RecoveryAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='server-recovery-api-')
        self.root = Path(self.temp.name)
        self.client = Client(self.root / 'server')
        self.launcher = StandaloneLauncher(self.client.home, service_factory='conversation_manager.fixture_sdk:factory')
        self.ref = None

    async def asyncTearDown(self):
        if self.ref: await self.launcher.terminate(self.ref['runtime_ref'], operation_id='cleanup')
        self.temp.cleanup()

    async def saved(self):
        self.prepared = PreparedLaunch(backend_id='claude', adapter_id='claude-sdk', protocol='claude-agent-sdk', cwd=str(self.root),
            argv=[], transport={'kind': 'sdk'})
        launched = await self.client.launch(self.prepared, launcher=self.launcher, operation_id='launch')
        self.assertEqual(launched['status'], 'completed', launched)
        self.ref = launched['result']['connection_ref']
        connection = await self.client.connect(self.ref)
        created = await connection.create_session({}, operation_id='create')
        self.assertEqual(created['status'], 'completed', created)
        return connection, created['result']['session_ref']

    async def test_plan_reads_saved_settings_and_only_explicit_environment_references(self):
        connection, session = await self.saved()
        await connection.disconnect()
        saved = self.client.store.get('session', session['session_id'])
        saved['settings']['mcp'] = {'servers': [{'env_vars': ['CAPTURED_ENV']}]}
        self.client.store.put('session', session['session_id'], saved)
        before = self.client.store.path.read_bytes()
        plan = await self.client.prepare_restore(session, self.prepared,
            env={'CAPTURED_ENV': 'explicit'}, ambient_env={'CAPTURED_ENV': 'ambient', 'UNRELATED': 'private'},
            credential_refs=[{'provider': 'fixture', 'home': '/selected'}])
        self.assertEqual(plan.settings, saved['settings'])
        self.assertIsNone(plan.connection_id)
        self.assertEqual(plan.env, {'CAPTURED_ENV': 'explicit'})
        self.assertNotIn('explicit', str(plan.to_dict()))
        self.assertEqual(self.client.store.path.read_bytes(), before)
        plan.settings['cwd'] = '/changed'
        self.assertEqual(self.client.store.get('session', session['session_id'])['settings']['cwd'], str(self.root))
        with self.assertRaises(ConversationError):
            await self.client.prepare_restore({**session, 'cwd': '/other'}, self.prepared)

    async def test_history_and_turn_result_read_saved_data_without_connecting(self):
        connection, session = await self.saved()
        sent = await connection.send(session, 'fixture message', operation_id='send')
        async with asyncio.timeout(5):
            while (await self.client.operation('send'))['status'] == 'accepted': await asyncio.sleep(.02)
        await connection.disconnect()
        self.assertEqual((await self.client.history(session))['session_ref'], session)
        artifact = await self.client.turn_result(session, sent['result']['turn_id'])
        self.assertTrue(artifact)
        with self.assertRaises(ConversationError): await self.client.history({**session, 'native_session_id': 'foreign'})

