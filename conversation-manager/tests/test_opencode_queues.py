"""Installed OpenCode, real shell tool, public SDK; deterministic local inference."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import threading
import unittest

import test_opencode_frontend as frontend


@unittest.skipUnless(os.name=='posix' and frontend.Manager and shutil.which('opencode'), 'Requires installed OpenCode and Agent Manager')
class OpenCodeQueueQualification(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=frontend.OpenCodeFrontendTests.asyncSetUp
    asyncTearDown=frontend.OpenCodeFrontendTests.asyncTearDown
    start=frontend.OpenCodeFrontendTests.start

    async def wait_state(self, chat, predicate):
        async with asyncio.timeout(25):
            while True:
                state=await chat.state()
                if predicate(state):return state
                await asyncio.sleep(.03)

    async def test_model_after_turn_queue_and_reconnect(self):
        with self.manager.lease_native_environment(self.identifier) as lease:
            path=Path(lease.env['OPENCODE_CONFIG_DIR'])/'opencode.json'
            config=json.loads(path.read_text());config['providers']['fixture']['models']['fixture-two']={'name':'Fixture two','limit':{'context':32000,'output':1024}}
            path.write_text(json.dumps(config))
        chat=await self.start()
        changed=await chat.set_config('model','fixture/fixture-two',operation_id='choose-model')
        self.assertEqual(changed['status'],'completed',changed)
        config=await chat.configuration()
        self.assertEqual(next(option for option in config['options'] if option['id']=='model')['currentValue'],'fixture/fixture-two')
        sent=await chat.send('Original',operation_id='send')
        async with asyncio.timeout(20):
            while not self.received.is_set():await asyncio.sleep(.02)
        queued=await chat.enqueue('Queued followup',expected_turn_id=sent['result']['turn_id'],operation_id='queued')
        self.assertEqual(queued['status'],'completed',queued)
        await chat.disconnect()
        restored=await self.client.attach_conversation(chat.reference);self.conversations.append(restored)
        self.release.set()
        state=await self.wait_state(restored,lambda s:not s['active_turn'] and s['queue']['items'][0]['status']=='delivered')
        followup=state['queue']['items'][0]['turn_id']
        self.assertNotEqual(followup,sent['result']['turn_id'])
        self.assertEqual((await restored.turn_result(followup))['status'],'completed')
        self.assertEqual((await restored.inspect_queued('queued'))['outcome'],'completed')

    async def test_observed_real_shell_tool_steers_original_turn(self):
        owner=self
        self.tool_sent=False;self.tool_contract=None;self.following=threading.Event()
        def handler(request):
            body=json.loads(request.rfile.read(int(request.headers['Content-Length'])))
            owner.requests.append(body)
            request.send_response(200);request.send_header('Content-Type','text/event-stream');request.end_headers()
            tool_delta=None
            if not owner.tool_sent:
                tools=[tool['function'] for tool in body.get('tools',[])]
                tool=next((tool for tool in tools if tool['name'].lower() in ('bash','shell')),None)
                owner.tool_contract=[tool['name'] for tool in tools]
                if tool:
                    owner.tool_sent=True
                    args={'command':'sleep 3; printf CM_NATIVE_TOOL_DONE','description':'Isolated native queue qualification','timeout':10000}
                    if tool.get('parameters',{}).get('properties',{}).get('command',{}).get('type')=='array':
                        args['command']=['sh','-c',args['command']]
                    tool_delta={'role':'assistant','tool_calls':[{'index':0,'id':'call_cm_native_tool','type':'function','function':{'name':tool['name'],'arguments':json.dumps(args)}}]}
            if tool_delta is None:
                owner.following.set();owner.release.wait(20)
            rows=[(tool_delta,'tool_calls')] if tool_delta else [({'role':'assistant','content':'CM_NATIVE_FINISHED'},None),({},'stop')]
            try:
                for delta,finish in rows:
                    row={'id':'cm-fixture','object':'chat.completion.chunk','created':1,'model':'fixture','choices':[{'index':0,'delta':delta,'finish_reason':finish}]}
                    request.wfile.write(('data: '+json.dumps(row)+'\n\n').encode());request.wfile.flush()
                request.wfile.write(b'data: [DONE]\n\n');request.wfile.flush()
            except (BrokenPipeError,ConnectionResetError):pass
        self.server.RequestHandlerClass.do_POST=handler
        chat=await self.start()
        sent=await chat.send('native-tool-qualification',operation_id='send')
        # Only the test-controlled sleep/printf command can be requested by this local fixture.
        async with asyncio.timeout(20):
            while True:
                pending=await chat.pending_requests()
                for request in pending:
                    self.assertEqual(request['kind'],'permission')
                    await chat.respond(request['request_id'],{'outcome':'selected','option_id':'once'},operation_id='approve:'+request['request_id'])
                state=await chat.state()
                if state['queue']['running_tools'] and not pending:break
                if not state['active_turn']:self.fail('No running native shell tool; advertised tools: '+str(self.tool_contract))
                await asyncio.sleep(.03)
        queued=await chat.enqueue('CM_STEER_AFTER_NATIVE_TOOL',mode='next_tool_completion',expected_turn_id=sent['result']['turn_id'],operation_id='guidance')
        self.assertEqual(queued['status'],'completed',queued)
        if queued['result']['paused']:
            resumed=await chat.resume_queue(operation_id='resume-after-approval')
            self.assertEqual(resumed['status'],'completed',resumed)
        await chat.disconnect()
        restored=await self.client.attach_conversation(chat.reference);self.conversations.append(restored)
        state=await self.wait_state(restored,lambda s:s['queue']['items'][0]['status'] in ('delivered','failed','uncertain'))
        item=state['queue']['items'][0]
        self.assertEqual(item['status'],'delivered',item)
        self.assertEqual(item['turn_id'],sent['result']['turn_id'])
        self.release.set()
        await self.wait_state(restored,lambda s:not s['active_turn'])
        result=await restored.turn_result(sent['result']['turn_id']);self.assertEqual(result['status'],'completed',result)
        inputs=[event for event in (await restored.history(limit=500))['entries'] if event['kind']=='message.input']
        self.assertEqual(len(inputs),1)
        self.assertEqual(inputs[0]['turn_id'],sent['result']['turn_id'])
        self.assertTrue(any('CM_STEER_AFTER_NATIVE_TOOL' in json.dumps(body) for body in self.requests))
