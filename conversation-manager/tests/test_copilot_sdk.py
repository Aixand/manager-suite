"""Copilot model control verifies state rather than trusting acknowledgments."""
import asyncio
import tempfile
import sqlite3
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from conversation_manager import ConversationError
from conversation_manager.adapters.copilot_sdk import CopilotDriver
from conversation_manager.store import Store


class CopilotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.callback = SimpleNamespace(emit=lambda *a, **k: None, question=AsyncMock(), permission=AsyncMock(return_value=False))
        self.driver = CopilotDriver({'cwd': self.temp.name, 'model': 'one'}, 'original-session', Path(self.temp.name),
            False, self.callback, {}, authentication={'method': 'copilot-login'})
        self.account = dict(isAuthenticated=True, authType='user', host='github.com', login='selected-user')
        self.models = [dict(id='one', name='One'), dict(id='two', name='Two'),
                       dict(id='blocked', name='Blocked', policy={'state': 'disabled'})]
        self.api = SimpleNamespace(list=AsyncMock(), get_current=AsyncMock(return_value={'modelId': 'one'}),
                                   switch_to=AsyncMock(return_value={'modelId': 'two'}))
        self.driver.client = SimpleNamespace(get_auth_status=AsyncMock(side_effect=lambda: dict(self.account)),
                                            list_models=AsyncMock(side_effect=lambda: self.models))
        self.driver.session = SimpleNamespace(rpc=SimpleNamespace(model=self.api))
        await self.driver.check_account()
        await self.driver.catalog()
        await self.driver.current_model()
        # Public generated request type is only needed when dispatching a switch.
        self.requests = patch.dict('sys.modules', {'copilot.generated.rpc': SimpleNamespace(ModelSwitchToRequest=SimpleNamespace)})
        self.requests.start()
        self.addCleanup(self.requests.stop)

    async def test_model_change_requires_matching_readback(self):
        with self.assertRaises(ConversationError) as error:
            await self.driver.set_model('two')
        self.assertTrue(error.exception.uncertain)
        self.assertEqual(self.driver.settings['model'], 'one')
        self.assertTrue(self.driver.configuration_uncertain)
        self.api.get_current.return_value = {'modelId': 'two'}
        result = await self.driver.set_model('two')
        self.assertEqual(result['options'][0]['currentValue'], 'two')
        self.assertEqual(self.driver.settings['model'], 'two')
        sent = self.api.switch_to.call_args.args[0]
        self.assertEqual(sent.model_change_scope, 'session')
        self.assertTrue(sent.require_available)

    async def test_unknown_or_disabled_models_are_rejected_before_dispatch(self):
        for value in ('missing', 'blocked'):
            with self.assertRaises(ConversationError) as error:
                await self.driver.set_model(value)
            self.assertEqual(error.exception.code, 'validation')
        self.api.switch_to.assert_not_called()

    async def test_deferred_and_persistence_failure_remain_uncertain(self):
        for reply in ({'deferred': True, 'queueId': 'original-queue'}, {'persistenceError': 'write failed'}):
            self.api.switch_to.return_value = reply
            with self.assertRaises(ConversationError) as error:
                await self.driver.set_model('two')
            self.assertTrue(error.exception.uncertain)
            self.assertEqual(self.driver.settings['model'], 'one')
        self.assertEqual(self.api.switch_to.await_count, 2)

    async def test_account_change_blocks_model_switch(self):
        self.account['login'] = 'different-user'
        with self.assertRaises(ConversationError) as error:
            await self.driver.set_model('two')
        self.assertEqual(error.exception.code, 'conflict')
        self.api.switch_to.assert_not_called()

    async def test_compaction_requires_an_explicit_decision_then_readback(self):
        self.api.switch_to.side_effect = [
            {'status': 'confirmation_required', 'confirmation': {'targetModelDisplayName': 'Two'}},
            {'modelId': 'two'},
        ]
        self.callback.question.return_value = {'model-compaction': 'Compact history and switch'}
        self.api.get_current.return_value = {'modelId': 'two'}
        await self.driver.set_model('two')
        requests = [call.args[0] for call in self.api.switch_to.call_args_list]
        self.assertIsNone(requests[0].compaction_decision)
        self.assertEqual(requests[1].compaction_decision, 'compact')
        self.assertTrue(requests[0].run_compaction_preflight)
        self.callback.question.assert_awaited_once()
        self.assertTrue(self.callback.question.call_args.kwargs['manager_owned'])
        self.assertEqual(self.driver.settings['model'], 'two')

    async def test_declining_compaction_preserves_the_current_model(self):
        self.api.switch_to.side_effect = [{'status': 'confirmation_required'}, {'status': 'cancelled'}]
        self.callback.question.return_value = {'model-compaction': 'Keep current model'}
        with self.assertRaises(ConversationError) as error:
            await self.driver.set_model('two')
        self.assertEqual(error.exception.code, 'cancelled')
        self.assertEqual(self.api.switch_to.call_args.args[0].compaction_decision, 'cancel')
        self.assertFalse(self.driver.configuration_uncertain)
        self.assertEqual(self.driver.settings['model'], 'one')

    async def test_rejection_does_not_claim_an_unchanged_model_was_applied(self):
        self.api.switch_to.return_value = {'status': 'rejected'}
        with self.assertRaises(ConversationError) as error:
            await self.driver.set_model('one')
        self.assertEqual(error.exception.code, 'provider_rejected')
        self.assertFalse(error.exception.uncertain)
        self.assertFalse(self.driver.configuration_uncertain)

    async def test_known_rpc_rejection_does_not_block_later_prompts(self):
        self.api.switch_to.side_effect = ConversationError('provider_rejected', 'Not available')
        with self.assertRaises(ConversationError):
            await self.driver.set_model('two')
        self.assertFalse(self.driver.configuration_uncertain)

    async def test_missing_readback_is_not_success(self):
        self.api.get_current.return_value = {}
        with self.assertRaises(ConversationError) as error:
            await self.driver.current_model()
        self.assertTrue(error.exception.uncertain)

    async def test_new_session_can_defer_its_default_model_without_blocking_prompts(self):
        self.driver.settings.pop('model')
        self.driver.model = None
        self.api.get_current.return_value = {}
        self.driver.connect = AsyncMock()
        self.driver.session.session_id = self.driver.identity
        self.driver.session.on = lambda handler: lambda: None
        self.driver.client.create_session = AsyncMock(return_value=self.driver.session)
        self.assertEqual(await self.driver.start(), self.driver.identity)
        self.assertNotIn('model', self.driver.client.create_session.call_args.kwargs)
        config = await self.driver.configuration()
        self.assertTrue(config['options'][0]['defaultUnknown'])
        self.assertEqual(config['options'][0]['currentValue'], '')
        self.assertFalse(self.driver.configuration_uncertain)
        self.api.get_current.return_value = {'modelId': 'two'}
        self.assertEqual((await self.driver.configuration())['options'][0]['currentValue'], 'two')

    async def test_empty_default_does_not_hide_an_unconfirmed_explicit_switch(self):
        self.driver.settings.pop('model')
        self.driver.model = None
        self.api.get_current.return_value = {}
        with self.assertRaises(ConversationError):
            await self.driver.set_model('two')
        self.assertTrue(self.driver.configuration_uncertain)


    async def test_subagent_events_cannot_complete_root_turn(self):
        gate = asyncio.Event()
        async def send(text, timeout):
            self.driver.observe(SimpleNamespace(agent_id='child', type='session.idle', data={}))
            await gate.wait()
            return SimpleNamespace(data={'content': 'Final root report', 'messageId': 'root'})
        self.driver.session.send_and_wait = send
        task = asyncio.create_task(self.driver.prompt('work'))
        await asyncio.sleep(0.01)
        self.assertFalse(task.done())
        gate.set()
        result = await asyncio.wait_for(task, 1)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['final_text'], 'Final root report')

    async def test_discovery_never_creates_a_session(self):
        self.driver.discovery = True
        self.driver.connect = AsyncMock()
        self.driver.client.create_session = AsyncMock()
        self.assertEqual(await self.driver.start(), self.driver.identity)
        self.driver.client.create_session.assert_not_called()


class ConfigurationCommitTests(unittest.TestCase):
    def test_related_checkpoint_updates_roll_back_together(self):
        with tempfile.TemporaryDirectory() as home:
            store = Store(home)
            store.put('session', 'session', {'model': 'one'})
            store.put('sdk-session', 'checkpoint', {'model': 'one'})
            with store.db(True) as db:
                db.execute("CREATE TRIGGER fail_checkpoint BEFORE INSERT ON objects WHEN NEW.kind='sdk-session' BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                store.put_many([('session', 'session', {'model': 'two'}), ('sdk-session', 'checkpoint', {'model': 'two'})])
            self.assertEqual(store.get('session', 'session'), {'model': 'one'})
            self.assertEqual(store.get('sdk-session', 'checkpoint'), {'model': 'one'})
