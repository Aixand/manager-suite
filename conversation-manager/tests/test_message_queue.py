import asyncio
import tempfile
import unittest
from conversation_manager import Client, PreparedLaunch, StandaloneLauncher


class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = Client(self.tmp.name)
        self.launcher = StandaloneLauncher(self.tmp.name, service_factory='conversation_manager.fixture_sdk:factory')
        launched = await self.client.launch(PreparedLaunch(backend_id='claude', adapter_id='claude-sdk',
            protocol='claude-agent-sdk', argv=[], cwd=self.tmp.name, transport={'kind':'sdk'}),
            launcher=self.launcher, operation_id='launch')
        self.ref = launched['result']['connection_ref']
        self.connection = await self.client.connect(self.ref)
        self.chat = await self.connection.create_conversation(operation_id='create')

    async def asyncTearDown(self):
        await self.connection.disconnect()
        await self.launcher.terminate(self.ref['runtime_ref'], operation_id='stop')
        self.tmp.cleanup()

    async def wait_for(self, predicate):
        async with asyncio.timeout(10):
            while True:
                value = await self.chat.queue_state()
                if predicate(value):
                    return value
                await asyncio.sleep(.02)

    async def test_fifo_and_delivery_without_frontend(self):
        turn = (await self.chat.send('slow', operation_id='send'))['result']['turn_id']
        first = await self.chat.enqueue('first', expected_turn_id=turn, operation_id='q1')
        self.assertEqual(first['status'], 'completed', first)
        self.assertEqual(first, await self.chat.enqueue('first', expected_turn_id=turn, operation_id='q1'))
        await self.chat.enqueue('second', expected_turn_id=turn, operation_id='q2')
        await self.connection.disconnect()
        await asyncio.sleep(.8)
        self.connection = await self.client.connect(self.ref)
        self.chat = await self.connection.attach_conversation(self.chat.reference)
        value = await self.wait_for(lambda q: len(q['items']) == 2 and all(i['status']=='delivered' for i in q['items']))
        history = (await self.chat.history(limit=500))['entries']
        inputs = [e['payload']['text'] for e in history if e['kind']=='message.input']
        self.assertEqual(inputs, ['first','second'])
        self.assertFalse(value['modes']['after_tool']['supported'])
        for item in value['items']:
            async with asyncio.timeout(10):
                while (await self.client.operation(item['delivery_operation_id']))['status']=='accepted':
                    await asyncio.sleep(.01)
            result = await self.chat.turn_result(item['turn_id'])
            self.assertEqual(result['status'], 'completed')

    async def test_queue_edits_check_the_worker_revision_atomically(self):
        turn = (await self.chat.send('hold', operation_id='send'))['result']['turn_id']
        await self.chat.enqueue('original', expected_turn_id=turn, operation_id='queued')
        self.assertEqual((await self.chat.queue_state())['items'][0]['revision'], 1)
        edited = await self.chat.edit_queued('queued', 'first editor', expected_revision=1, operation_id='edit-first')
        self.assertEqual(edited['status'], 'completed', edited)
        stale = await self.chat.edit_queued('queued', 'stale editor', expected_revision=1, operation_id='edit-stale')
        self.assertEqual(stale['status'], 'failed', stale)
        self.assertEqual(stale['error']['code'], 'conflict')
        item = (await self.chat.queue_state())['items'][0]
        self.assertEqual((item['text'], item['revision']), ('first editor', 2))
        removed = await self.chat.remove_queued('queued', expected_revision=2, operation_id='remove')
        self.assertEqual(removed['status'], 'completed', removed)

    async def test_cancel_pauses_edit_remove_resume(self):
        turn = (await self.chat.send('hold', operation_id='send'))['result']['turn_id']
        await self.chat.enqueue('old', expected_turn_id=turn, operation_id='q1')
        await self.chat.enqueue('remove', expected_turn_id=turn, operation_id='q2')
        await self.chat.edit_queued('q1', 'edited', operation_id='edit')
        await self.chat.remove_queued('q2', operation_id='remove')
        await self.chat.cancel(turn, operation_id='cancel')
        value = await self.wait_for(lambda q:q['paused'])
        self.assertEqual(value['items'][0]['status'],'queued')
        await asyncio.sleep(.1)
        self.assertIsNone((await self.chat.state())['active_turn'])
        resumed = await self.chat.resume_queue(operation_id='resume')
        self.assertEqual(resumed['status'],'completed',resumed)
        value = await self.wait_for(lambda q:q['items'][0]['status']=='delivered')
        self.assertEqual(value['items'][0]['text'],'edited')
        self.assertEqual(value['items'][1]['status'],'removed')

    async def test_approval_and_unsupported_boundary(self):
        turn = (await self.chat.send('permission',operation_id='send'))['result']['turn_id']
        async with asyncio.timeout(10):
            while not await self.chat.pending_requests():
                await asyncio.sleep(.01)
        result=await self.chat.enqueue('later',expected_turn_id=turn,operation_id='q1')
        self.assertTrue(result['result']['paused'])
        result=await self.chat.resume_queue(operation_id='blocked')
        self.assertEqual(result['status'],'failed')
        result=await self.chat.enqueue('tool',mode='after_tool',expected_turn_id=turn,operation_id='tool')
        self.assertEqual(result['status'],'unsupported')
        pending=(await self.chat.pending_requests())[0]
        await self.chat.respond(pending['request_id'],{'outcome':'selected','option_id':'deny_once'},operation_id='deny')
        await asyncio.sleep(.15)
        self.assertEqual((await self.chat.queue_state())['items'][0]['status'],'queued')

    async def test_turn_identity_and_edit_receipt_conflict(self):
        result=await self.chat.enqueue('later',expected_turn_id='missing',operation_id='no-turn')
        self.assertEqual(result['status'],'failed')
        turn=(await self.chat.send('hold',operation_id='send'))['result']['turn_id']
        await self.chat.enqueue('first',expected_turn_id=turn,operation_id='q1')
        from conversation_manager import ConversationError
        with self.assertRaises(ConversationError):
            await self.chat.enqueue('different',expected_turn_id=turn,operation_id='q1')
        self.assertEqual(len((await self.chat.queue_state())['items']),1)

    async def test_failed_turn_keeps_followups_paused(self):
        turn=(await self.chat.send('queue-demo',operation_id='send'))['result']['turn_id']
        await self.chat.enqueue('limit',expected_turn_id=turn,operation_id='q1')
        await self.chat.enqueue('must stay queued',expected_turn_id=turn,operation_id='q2')
        value=await self.wait_for(lambda q:q['paused'])
        self.assertEqual(value['items'][1]['status'],'queued')
        self.assertEqual(value['items'][0]['status'],'delivered')
        await asyncio.sleep(.05)
        self.assertIsNone((await self.chat.state())['active_turn'])


class DispatchRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_uncertain_dispatch_is_persisted_and_never_replayed(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from conversation_manager.message_queue import MessageQueue
        from conversation_manager.store import Store
        with tempfile.TemporaryDirectory() as home:
            core=SimpleNamespace(store=Store(home),active={},lost=False,draining=False,requests={},
                emit=lambda *args,**kw:None,adapter=SimpleNamespace(operations={'send':{'supported':True}}),
                mutate=AsyncMock(return_value={'status':'uncertain','result':None}))
            session={'session_id':'session','native_session_id':'native'}
            queue=MessageQueue(core)
            value=queue.get(session)
            value['items']=[{'queue_id':'q1','text':'once only','mode':'after_turn','status':'queued','created_at':'1'}]
            queue.save(session,value)
            await queue.deliver(session)
            self.assertEqual(core.mutate.await_count,1)
            replacement=MessageQueue(core)
            self.assertTrue(replacement.view(session)['paused'])
            self.assertEqual(replacement.view(session)['items'][0]['status'],'uncertain')
            await replacement.deliver(session)
            self.assertEqual(core.mutate.await_count,1)

    async def test_interrupted_dispatch_is_not_replayed_or_resumed(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from conversation_manager.message_queue import MessageQueue
        from conversation_manager.store import Store
        from conversation_manager import ConversationError
        with tempfile.TemporaryDirectory() as home:
            core=SimpleNamespace(store=Store(home),active={},lost=False,draining=False,requests={},
                emit=lambda *args,**kw:None,adapter=SimpleNamespace(operations={}),mutate=AsyncMock())
            session={'session_id':'session','native_session_id':'native'}
            queue=MessageQueue(core)
            queue.save(session,{'paused':False,'reason':None,'items':[{'queue_id':'q1','text':'retained',
                'status':'dispatching','created_at':'1'}]})
            replacement=MessageQueue(core)
            await replacement.deliver(session)
            core.mutate.assert_not_awaited()
            with self.assertRaises(ConversationError):
                replacement.mutate(session,{'action':'resume'})


class QueueRecoveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = QueueTests.asyncSetUp
    asyncTearDown = QueueTests.asyncTearDown
    async def test_inspection_works_with_frontend_detached(self):
        turn=(await self.chat.send('queue-demo',operation_id='send'))['result']['turn_id']
        await self.chat.enqueue('later',expected_turn_id=turn,operation_id='q1')
        await self.connection.disconnect()
        evidence=await self.chat.inspect_queued('q1')
        self.assertEqual(evidence['outcome'],'not_dispatched')
        self.assertFalse(evidence['can_reconcile'])
        recorded=await self.client.recorded_queue(self.chat.reference)
        self.assertEqual(recorded['source'],'last-recorded')
        self.assertEqual(recorded['items'][0]['text'],'later')
        self.connection=await self.client.connect(self.ref)

    async def test_reconcile_matching_handoff_without_dispatch(self):
        from conversation_manager.models import receipt
        from conversation_manager.store import now
        session=self.chat.reference
        op='queue-send:recovery'
        intent={'connection_id':session['connection_id'],'method':'send','params':{
            'session_ref':session,'content':[{'type':'text','text':'retained'}]}}
        self.client.store.claim(op,intent)
        self.client.store.finish(receipt(op,'accepted',result={'turn_id':'known-turn'}))
        self.client.store.put('message-queue',session['session_id'],{'paused':True,'reason':'interrupted',
            'items':[{'queue_id':'q1','text':'retained','mode':'after_turn','status':'dispatching',
                      'created_at':now(),'delivery_operation_id':op}]})
        evidence=await self.chat.inspect_queued('q1')
        self.assertEqual(evidence['outcome'],'accepted')
        self.assertTrue(evidence['can_reconcile'])
        result=await self.chat.reconcile_queued('q1',operation_id='reconcile')
        self.assertEqual(result['status'],'completed',result)
        self.assertTrue(result['result']['paused'])
        self.assertEqual(result['result']['items'][0]['status'],'delivered')
        self.assertIsNone((await self.chat.state())['active_turn'])
        self.assertEqual(result,await self.chat.reconcile_queued('q1',operation_id='reconcile'))
        inputs=[e for e in (await self.chat.history(limit=500))['entries'] if e['kind']=='message.input']
        self.assertEqual(len(inputs),1)
        self.assertEqual(inputs[0]['turn_id'],'known-turn')

    async def test_reconcile_refuses_missing_or_conflicting_receipt(self):
        from conversation_manager.models import receipt
        from conversation_manager.store import now
        session=self.chat.reference
        self.client.store.put('message-queue',session['session_id'],{'paused':True,'reason':'interrupted',
            'items':[{'queue_id':'q1','text':'retained','mode':'after_turn','status':'dispatching',
                      'created_at':now(),'delivery_operation_id':'missing'}]})
        result=await self.chat.reconcile_queued('q1',operation_id='blocked1')
        self.assertEqual(result['status'],'uncertain')
        self.client.store.claim('missing',{'connection_id':'another-session','method':'send'})
        self.client.store.finish(receipt('missing','accepted',result={'turn_id':'unrelated'}))
        evidence=await self.chat.inspect_queued('q1')
        self.assertEqual(evidence['outcome'],'intent_conflict')
        self.assertFalse(evidence['can_reconcile'])
        result=await self.chat.reconcile_queued('q1',operation_id='blocked2')
        self.assertEqual(result['status'],'uncertain')
        self.assertIsNone((await self.chat.state())['active_turn'])

    async def test_uncertain_matching_receipt_stays_blocked(self):
        from conversation_manager.models import receipt
        from conversation_manager.store import now
        session=self.chat.reference
        op='queue-send:uncertain'
        self.client.store.claim(op,{'connection_id':session['connection_id'],'method':'send','params':{
            'session_ref':session,'content':[{'type':'text','text':'retained'}]}})
        self.client.store.finish(receipt(op,'uncertain',result={'turn_id':'unconfirmed-turn'},
            error={'code':'transport','uncertain':True}))
        self.client.store.put('message-queue',session['session_id'],{'paused':True,'reason':'interrupted',
            'items':[{'queue_id':'q1','text':'retained','mode':'after_turn','status':'dispatching',
                      'created_at':now(),'delivery_operation_id':op}]})
        evidence=await self.chat.inspect_queued('q1')
        self.assertEqual(evidence['outcome'],'unconfirmed')
        self.assertFalse(evidence['can_reconcile'])
        result=await self.chat.reconcile_queued('q1',operation_id='blocked')
        self.assertEqual(result['status'],'uncertain')
        self.assertIsNone((await self.chat.state())['active_turn'])
        await self.launcher.terminate(self.ref['runtime_ref'],operation_id='stop-before-inspect')
        evidence=await self.client.inspect_queued(session,'q1')
        self.assertEqual(evidence['outcome'],'unconfirmed')
        self.assertEqual((await self.client.recorded_queue(session))['source'],'last-recorded')
