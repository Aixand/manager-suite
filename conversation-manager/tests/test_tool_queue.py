import asyncio
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from conversation_manager import ConversationError
from conversation_manager.message_queue import MessageQueue
from conversation_manager.store import Store
import test_message_queue as queue_tests


class ToolQueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.session={'session_id':'s','native_session_id':'n'}
        self.turn={'turn_id':'t','native_turn_id':'native-t','cancel_operations':[]}
        self.core=SimpleNamespace(store=Store(self.tmp.name),active={'s':self.turn},lost=False,draining=False,
            requests={},adapter=SimpleNamespace(operations={'send':{'supported':True},'steer':{'supported':True}}),
            emit=lambda *args,**kwargs:None,
            mutate=AsyncMock(return_value={'status':'completed','result':{'turn_id':'t'}}))
        self.tasks=[]
        self.core.spawn=lambda coro:self.tasks.append(asyncio.create_task(coro))
        self.core.supported=lambda name:None
        self.queue=MessageQueue(self.core)

    async def asyncTearDown(self):
        if self.tasks:
            await asyncio.gather(*self.tasks)

    def event(self, tool, status, kind='tool.update',native=None):
        self.queue.observe(self.session,self.turn,kind,{'tool_call_id':tool,'status':status},native)

    def enqueue(self, key='q1'):
        return self.queue.mutate(self.session,{'action':'enqueue','mode':'next_tool_completion',
            'expected_turn_id':'t','operation_id':key,'text':key})

    async def test_parallel_snapshot_later_tools_and_fifo(self):
        self.event('one','inProgress','tool.start')
        self.event('two','running')
        self.enqueue();self.enqueue('q2')
        self.event('one','completed','tool.result')
        self.event('later','running')
        await asyncio.sleep(0)
        self.core.mutate.assert_not_awaited()
        self.event('two','error')
        async with asyncio.timeout(2):
            while self.core.mutate.await_count<2:
                await asyncio.sleep(.01)
        calls=self.core.mutate.call_args_list
        self.assertEqual([call.args[0] for call in calls],['steer','steer'])
        self.assertEqual([call.args[1]['text'] for call in calls],['q1','q2'])
        self.assertTrue(all(call.args[1]['expected_turn_id']=='t' for call in calls))
        self.assertEqual(self.queue.view(self.session)['running_tools'],['later'])
        self.event('two','completed','tool.result')
        await asyncio.sleep(.01)
        self.assertEqual(self.core.mutate.await_count,2)

    async def test_no_tools_stale_native_event_and_missing_steering(self):
        with self.assertRaises(ConversationError):self.enqueue()
        self.event('old','running',native={'turnId':'other-turn'})
        self.assertEqual(self.queue.view(self.session)['running_tools'],[])
        self.core.adapter.operations['steer']['supported']=False
        self.assertFalse(self.queue.modes()['next_tool_completion']['supported'])

    async def test_turn_ends_before_delivery_keeps_message_and_blocks_new_turn(self):
        self.event('one','running');self.enqueue()
        self.event('one','completed')
        self.core.active.clear()
        await self.queue.finished(self.session,'completed')
        await self.queue.deliver(self.session)
        self.core.mutate.assert_not_awaited()
        value=self.queue.view(self.session)
        self.assertTrue(value['paused'])
        self.assertEqual(value['items'][0]['status'],'queued')
        self.assertIn('ended',value['items'][0]['blocked_reason'])
        with self.assertRaises(ConversationError):self.queue.mutate(self.session,{'action':'resume'})
        self.core.active['s']={**self.turn,'turn_id':'later'}
        with self.assertRaises(ConversationError):self.queue.mutate(self.session,{'action':'resume'})

    async def test_pause_observes_completions_and_resume_delivers_same_turn(self):
        self.event('one','running');self.enqueue()
        self.queue.pause(self.session,'approval')
        self.event('one','completed')
        await asyncio.sleep(.01)
        self.core.mutate.assert_not_awaited()
        self.assertEqual(self.queue.get(self.session)['items'][0]['remaining_tools'],[])
        self.queue.mutate(self.session,{'action':'resume'})
        await self.queue.deliver(self.session)
        self.core.mutate.assert_awaited_once()

    async def test_uncertain_steer_is_not_replayed(self):
        self.event('one','running');self.enqueue()
        self.core.mutate.return_value={'status':'uncertain','result':{'turn_id':'t'}}
        self.event('one','completed')
        await asyncio.sleep(.01)
        self.assertEqual(self.queue.get(self.session)['items'][0]['status'],'uncertain')
        await self.queue.deliver(self.session)
        self.core.mutate.assert_awaited_once()


class ToolQueueIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=queue_tests.QueueTests.asyncSetUp
    asyncTearDown=queue_tests.QueueTests.asyncTearDown
    wait_for=queue_tests.QueueTests.wait_for

    async def test_worker_steers_same_turn_after_snapshot_tools_while_detached(self):
        turn=(await self.chat.send('tool-queue-demo',operation_id='send'))['result']['turn_id']
        await self.wait_for(lambda q:len(q['running_tools'])==2)
        result=await self.chat.enqueue('guidance',mode='next_tool_completion',expected_turn_id=turn,operation_id='q1')
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(result,await self.chat.enqueue('guidance',mode='next_tool_completion',expected_turn_id=turn,operation_id='q1'))
        await self.connection.disconnect()
        await asyncio.sleep(3.3)
        self.connection=await self.client.connect(self.ref)
        self.chat=await self.connection.attach_conversation(self.chat.reference)
        value=await self.wait_for(lambda q:q['items'][0]['status']=='delivered')
        self.assertEqual(value['items'][0]['turn_id'],turn)
        self.assertEqual((await self.chat.state())['active_turn']['turn_id'],turn)
        self.assertEqual(value['running_tools'],['later-tool'])
        evidence=await self.chat.inspect_queued('q1')
        self.assertEqual(evidence['outcome'],'handed_off')
        history=(await self.chat.history(limit=500))['entries']
        inputs=[e for e in history if e['kind']=='message.input']
        self.assertEqual(len(inputs),1)
        self.assertEqual(inputs[0]['turn_id'],turn)
        self.assertEqual(inputs[0]['payload']['delivery_mode'],'next_tool_completion')

    async def test_parent_completion_does_not_prove_uncertain_steering(self):
        from conversation_manager.models import receipt
        sent=await self.chat.send('hello',operation_id='send')
        turn=sent['result']['turn_id']
        async with asyncio.timeout(5):
            while (await self.chat.state())['active_turn']:await asyncio.sleep(.01)
        session=self.chat.reference
        params={'session_ref':session,'text':'guidance','expected_turn_id':turn,
                'queue_id':'q1','delivery_mode':'next_tool_completion'}
        self.client.store.claim('uncertain-steer',{'connection_id':session['connection_id'],'method':'steer','params':params})
        self.client.store.finish(receipt('uncertain-steer','uncertain',result={'turn_id':turn},error={'uncertain':True}))
        self.client.store.put('message-queue',session['session_id'],{'paused':True,'reason':'unknown',
            'items':[{'queue_id':'q1','mode':'next_tool_completion','text':'guidance','target_turn_id':turn,
                      'status':'dispatching','delivery_operation_id':'uncertain-steer','created_at':'1'}]})
        evidence=await self.chat.inspect_queued('q1')
        self.assertEqual(evidence['turn_result']['status'],'completed')
        self.assertEqual(evidence['outcome'],'unconfirmed')
        self.assertFalse(evidence['can_reconcile'])
