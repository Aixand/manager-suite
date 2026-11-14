"""Startup evidence survives worker failures and launcher observation races."""
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from conversation_manager import Client, ConversationError, PreparedLaunch, StandaloneLauncher
from conversation_manager.models import receipt
from conversation_manager.store import Store
from conversation_manager.worker import serve


class StartupFailureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cm-start-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.client = Client(self.home)
        self.prepared = PreparedLaunch(backend_id='claude', adapter_id='claude-sdk',
            protocol='claude-agent-sdk', cwd=str(self.home), argv=[], transport={'kind': 'sdk'})

    async def test_service_import_crash_is_reported_and_retry_does_not_spawn(self):
        launcher = StandaloneLauncher(self.home, service_factory='missing_startup_service:factory')
        result = await self.client.launch(self.prepared, launcher=launcher, operation_id='start')
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(result['error']['code'], 'startup_failed')
        self.assertEqual(result['recovery']['startup']['phase'], 'services')
        self.assertEqual(result['recovery']['startup']['exception_type'], 'ModuleNotFoundError')
        with patch('conversation_manager.launcher.subprocess.Popen', side_effect=AssertionError('No replay')):
            self.assertEqual(await self.client.launch(self.prepared, launcher=launcher, operation_id='start'), result)

    def bootstrap(self):
        ref = dict(schema='conversation-manager/connection-ref-1', connection_id='connection',
            backend_id='claude', adapter_id='claude-sdk', protocol='claude-agent-sdk',
            runtime_ref={'owner': 'test', 'id': 'worker'},
            transport={'kind': 'conversation-manager-local', 'endpoint': str(self.home / 'worker.sock')})
        self.client.store.claim('start', {'method': 'launch'})
        return dict(home=str(self.home), prepared=self.prepared.to_dict(),
            connection_ref=ref, operation_id='start', env={'API_KEY': 'do-not-retain'})

    def core(self):
        core = MagicMock()
        core.adapter.start = AsyncMock()
        core.adapter.close = AsyncMock()
        core.shutdown = asyncio.Event()
        core.shutdown.set()
        core.epoch = "test-epoch"
        core.tasks = set()
        return core

    async def test_slow_initialization_is_not_reported_ready_early(self):
        bootstrap, core = self.bootstrap(), self.core()
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed():
            entered.set()
            await release.wait()
        core.adapter.start.side_effect = delayed
        with patch('conversation_manager.worker.Core', return_value=core), patch('conversation_manager.worker.install_signals'):
            task = asyncio.create_task(serve(bootstrap))
            await entered.wait()
            self.assertEqual(self.client.store.operation('start')['status'], 'uncertain')
            release.set()
            await task
        self.assertEqual(self.client.store.operation('start')['status'], 'completed')
        core.adapter.close.assert_awaited_once()

    async def test_startup_timeout_is_definitive_before_session_admission(self):
        bootstrap, core = self.bootstrap(), self.core()
        async def delayed():
            await asyncio.Event().wait()
        core.adapter.start.side_effect = delayed
        timeout = asyncio.timeout
        with patch('conversation_manager.worker.Core', return_value=core), patch(
                'conversation_manager.worker.asyncio.timeout', side_effect=lambda _: timeout(.01)):
            await serve(bootstrap)
        row = self.client.store.operation('start')
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['error']['code'], 'startup_timeout')
        self.assertEqual(row['recovery']['startup']['phase'], 'adapter')
        self.assertGreaterEqual(row['recovery']['startup']['elapsed_ms'], 0)
        core.adapter.close.assert_awaited_once()

    async def test_known_uncertain_startup_error_remains_uncertain_and_is_redacted(self):
        bootstrap, core = self.bootstrap(), self.core()
        core.adapter.start.side_effect = ConversationError('transport', 'Unavailable do-not-retain', uncertain=True)
        with patch('conversation_manager.worker.Core', return_value=core):
            await serve(bootstrap)
        row = self.client.store.operation('start')
        self.assertEqual(row['status'], 'uncertain')
        self.assertEqual(row['error']['code'], 'transport')
        self.assertNotIn('do-not-retain', str(row))
        self.assertIn('Unavailable', row['error']['message'])

    async def test_arbitrary_exception_text_is_not_persisted(self):
        bootstrap, core = self.bootstrap(), self.core()
        core.adapter.start.side_effect = RuntimeError('credential-that-is-not-in-env')
        with patch('conversation_manager.worker.Core', return_value=core):
            await serve(bootstrap)
        row = self.client.store.operation('start')
        self.assertEqual(row['status'], 'failed')
        self.assertIn('RuntimeError', row['error']['message'])
        self.assertNotIn('credential-that-is-not-in-env', str(row))

    async def test_launcher_timeout_cannot_overwrite_worker_evidence(self):
        store = self.client.store
        class RacingLauncher:
            async def launch(inner, prepared, *, operation_id):
                store.finish(receipt(operation_id, 'failed',
                    error=ConversationError('authentication', 'Sign in first').to_dict(),
                    recovery={'startup': {'phase': 'adapter'}}))
                return dict(status='uncertain', error=ConversationError('uncertain', 'Readiness unknown', uncertain=True).to_dict())
        result = await self.client.launch(self.prepared, launcher=RacingLauncher(), operation_id='race')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error']['code'], 'authentication')
        self.assertEqual(self.client.store.operation('race'), result)

    def test_launcher_observation_preserves_late_readiness(self):
        store = Store(self.home)
        store.claim('race', {'method': 'launch'})
        ready = store.finish(receipt('race', 'completed', result={'connection_ref': {'id': 'worker'}}))
        self.assertEqual(store.finish_launch(receipt('race', 'uncertain')), ready)

    def test_launcher_observation_preserves_uncertain_worker_diagnostic(self):
        store = Store(self.home)
        store.claim('race', {'method': 'launch'})
        error = store.finish(receipt('race', 'uncertain',
            error=ConversationError('transport', 'Provider unavailable', uncertain=True).to_dict(),
            recovery={'startup': {'phase': 'adapter'}}))
        self.assertEqual(store.finish_launch(receipt('race', 'uncertain')), error)
