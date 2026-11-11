"""Initialization and checkpoint failures retain different recovery evidence."""
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from conversation_manager import PreparedLaunch, ConversationError
from conversation_manager.adapters.sdk import SdkAdapter
from conversation_manager.store import Store


class CreationTests(unittest.IsolatedAsyncioTestCase):
    async def test_observed_identity_does_not_mislabel_initialization_as_a_checkpoint_failure(self):
        for checkpoint in (False, True):
            with self.subTest(checkpoint=checkpoint), tempfile.TemporaryDirectory() as home:
                store = Store(home)
                prepared = PreparedLaunch(backend_id='copilot', adapter_id='copilot-sdk', protocol='copilot-sdk',
                    argv=[], transport={'kind': 'sdk'}, cwd=home, env={})
                adapter = SdkAdapter(SimpleNamespace(store=store), prepared)
                adapter.adapter_version = 'fixture'
                driver = SimpleNamespace(identity_observed=True, settings={'cwd': home},
                    start=AsyncMock(return_value='original'), close=AsyncMock())
                if not checkpoint:
                    driver.start.side_effect = ConversationError('configuration', 'Explicit model was not confirmed', uncertain=True)
                with patch.object(adapter, 'driver', return_value=driver), patch.object(store, 'put', side_effect=OSError('disk failure')) as put:
                    with self.assertRaises(ConversationError) as caught:
                        await adapter.open({'cwd': home}, 'original')
                    error = caught.exception
                    self.assertTrue(error.uncertain)
                    self.assertEqual(error.recovery['native_session_id'], 'original')
                    self.assertEqual(error.recovery['phase'], 'checkpoint' if checkpoint else 'initialization')
                    if checkpoint:
                        self.assertIn('checkpoint could not be saved', str(error))
                        put.assert_called_once()
                    else:
                        self.assertIn('Explicit model was not confirmed', str(error))
                        self.assertNotIn('checkpoint', str(error))
                        put.assert_not_called()
