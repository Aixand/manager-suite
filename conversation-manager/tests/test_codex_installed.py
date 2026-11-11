"""Opt-in native Codex checks with disposable homes and no inference."""
import json
import asyncio
import os
from pathlib import Path
import tempfile
import unittest

from agent_manager import Manager
from conversation_manager import AgentEnvironments, Client, NativeCodexConnection, StandaloneLauncher
from conversation_manager.codex_runtime import CodexRuntime
from conversation_manager.provider_policy import cli_version, compatibility


@unittest.skipUnless(os.environ.get('CM_TEST_NATIVE_CODEX'), 'Set CM_TEST_NATIVE_CODEX to a native Codex executable')
class InstalledCodexTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cm-native-proof-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        config = self.root / 'providers.toml'
        config.write_text('[providers.codex]\nexecutable=' + json.dumps(str(Path(os.environ['CM_TEST_NATIVE_CODEX']).resolve())) + '\n')
        self.manager = Manager('codex', config=config, root=self.root / 'agents')
        self.manager.new_profile('native-check')
        self.identifier = self.manager.resolve_profile('native-check').environment_id
        self.agents = AgentEnvironments('codex', config=config, root=self.root / 'agents')

    async def test_native_initialization_account_status_and_thread_creation(self):
        runtime = CodexRuntime()
        self.addAsyncCleanup(runtime.close)
        transport = await runtime.start(self.agents.prepare_native(self.identifier), str(self.root))
        connection = NativeCodexConnection(None, transport=transport, timeout=30)
        self.addAsyncCleanup(connection.close)
        await connection.connect()
        version = cli_version(connection.initialization.get('userAgent', ''))
        self.assertTrue(compatibility('codex', version, backend='native-codex')['compatible'])
        account = await connection.request('account/read', {'refreshToken': False})
        self.assertIsNone(account.get('account'))
        thread = await connection.request('thread/start', {'cwd': str(self.root)})
        self.assertTrue(thread['thread']['id'])

    async def test_managed_start_refuses_a_signed_out_native_home(self):
        client = Client(self.root / 'conversations')
        result = await client.launch_codex(self.identifier, agents=self.agents, cwd=self.root, operation_id='signed-out')
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(result['error']['code'], 'authentication')
        owner = StandaloneLauncher(client.home)
        async with asyncio.timeout(8):
            while (await owner.inspect(result['recovery']['runtime_ref']))['state'] != 'stopped':
                await asyncio.sleep(.02)
        from agent_manager.storage.locks import locks
        with locks(self.manager.lock_root, 'environment-' + self.identifier):
            pass
