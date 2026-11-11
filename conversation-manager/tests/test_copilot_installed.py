"""Opt-in official SDK/runtime qualification against a localhost model, no provider credentials."""
import asyncio
import http.server
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
import sys

from conversation_manager import Client, PreparedLaunch, StandaloneLauncher
from conversation_manager.models import digest


class ModelHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append(request)
        hold = getattr(self.server, 'hold_next', None)
        if hold:
            self.server.hold_next = None
            self.server.request_started.set()
            hold.wait(10)
        tool = getattr(self.server, 'next_tool', None)
        self.server.next_tool = None
        if tool:
            marker, arguments = tool
            name = next(row['function']['name'] for row in request['tools'] if marker in row['function']['name'])
            delta = {'role': 'assistant', 'tool_calls': [dict(index=0, id='fixture-call-' + str(len(self.server.requests)),
                type='function', function=dict(name=name, arguments=json.dumps(arguments)))]}
        else:
            delta = {'role': 'assistant', 'content': 'Local fixture report'}
        chunks = [dict(id='fixture-response', object='chat.completion.chunk', model='gpt-4.1',
            choices=[dict(index=0, delta=delta, finish_reason=None)]),
            dict(id='fixture-response', object='chat.completion.chunk', model='gpt-4.1',
                 choices=[dict(index=0, delta={}, finish_reason='tool_calls' if tool else 'stop')])]
        body = ''.join('data: ' + json.dumps(row) + '\n\n' for row in chunks) + 'data: [DONE]\n\n'
        encoded = body.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Cancellation may close the model request before its response.


@unittest.skipUnless(os.environ.get('COPILOT_SDK_TEST_BIN'), 'Set COPILOT_SDK_TEST_BIN to qualify the installed SDK/runtime')
class InstalledCopilotTests(unittest.IsolatedAsyncioTestCase):
    async def test_retained_model_send_reconnect_and_exact_restore(self):
        self.assertEqual(importlib.metadata.version('github-copilot-sdk'), '1.0.26')
        with tempfile.TemporaryDirectory(prefix='copilot-sdk-installed-') as home:
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), ModelHandler)
            server.requests = []
            threading.Thread(target=server.serve_forever, daemon=True).start()
            launcher, client = StandaloneLauncher(home), Client(home)
            refs, connections = [], []
            root = Path(home)
            skill = root / 'audit'
            skill.mkdir()
            content = b'---\nname: audit\ndescription: COPILOT_CAPTURED_SKILL\n---\nReview the local fixture.\n'
            (skill / 'SKILL.md').write_bytes(content)
            manifest = dict(name='audit', directories=[], files=[dict(path='SKILL.md', size=len(content),
                digest='sha256:' + hashlib.sha256(content).hexdigest(), executable=False)])
            mcp = root / 'fixture_mcp.py'
            mcp.write_text('from mcp.server.fastmcp import FastMCP\nserver=FastMCP("fixture")\n@server.tool()\ndef captured_echo(message: str) -> str:\n return "CAPTURED_MCP:" + message\nserver.run(transport="stdio")\n')
            env = dict(PATH=os.environ['PATH'], HOME=home, COPILOT_HOME=home + '/copilot',
                COPILOT_OFFLINE='true', COPILOT_PROVIDER_TYPE='openai', COPILOT_PROVIDER_WIRE_API='completions',
                COPILOT_PROVIDER_BASE_URL=f'http://127.0.0.1:{server.server_port}/v1',
                COPILOT_PROVIDER_API_KEY='localhost-fixture-not-real', COPILOT_MODEL='gpt-4.1',
                AIXAND_COPILOT_CLI=str(Path(os.environ['COPILOT_SDK_TEST_BIN']).absolute()))
            prepared = PreparedLaunch(backend_id='copilot', adapter_id='copilot-sdk', protocol='copilot-sdk',
                transport={'kind': 'sdk'}, argv=[], cwd=home, settings={'model': 'gpt-4.1',
                    'instructions': {'text': 'COPILOT_CAPTURED_INSTRUCTION'},
                    'skills': [dict(name='audit', export=str(skill), manifest=manifest, digest=digest(manifest))],
                    'mcp': {'servers': [dict(name='managedfixture', command=sys.executable, args=[str(mcp)])]}},
                credential_refs=[dict(provider='copilot', method='copilot-byok', id='local-fixture', revision='one')], env=env)
            async def connect():
                result = await client.launch(prepared, launcher=launcher, operation_id=uuid.uuid4().hex)
                self.assertEqual(result['status'], 'completed', result)
                ref = result['result']['connection_ref']; refs.append(ref)
                connection = await client.connect(ref); connections.append(connection)
                return connection
            async def completion(key):
                async with asyncio.timeout(30):
                    while True:
                        result = await client.operation(key)
                        if result['status'] != 'accepted':
                            return result
                        await asyncio.sleep(.02)
            try:
                original = await connect()
                result = await original.create_session({}, operation_id='fixture-create')
                self.assertEqual(result['status'], 'completed', result)
                session = result['result']['session_ref']
                config = await original.configuration(session)
                self.assertEqual(config['options'][0]['currentValue'], 'gpt-4.1')
                changed = await original.set_model(session, 'gpt-4.1', operation_id='fixture-model')
                self.assertEqual(changed['status'], 'completed', changed)
                await original.send(session, 'Report the local fixture result', operation_id='fixture-send')
                receipt = await completion('fixture-send')
                self.assertEqual(receipt['status'], 'completed', receipt)
                self.assertEqual(receipt['result']['status'], 'completed')
                artifact = await original.turn_result(session, receipt['result']['turn_id'])
                self.assertEqual(artifact['final_text'], 'Local fixture report')
                self.assertTrue(server.requests)
                self.assertTrue(all(row['model'] == 'gpt-4.1' for row in server.requests))
                self.assertIn('COPILOT_CAPTURED_INSTRUCTION', json.dumps(server.requests[0]))
                self.assertIn('COPILOT_CAPTURED_SKILL', json.dumps(server.requests[0]))
                marker = root / 'denied-command-output'
                for scenario, tool, arguments in (
                    ('permission', 'bash', {'command': 'touch ' + str(marker), 'description': 'Create local fixture marker'}),
                    ('question', 'ask_user', {'question': 'Choose fixture', 'choices': ['One', 'Two']}),
                    ('mcp', 'captured_echo', {'message': 'local-report'}),
                ):
                    server.next_tool = (tool, arguments)
                    key = 'fixture-' + scenario
                    await original.send(session, 'Exercise local ' + scenario, operation_id=key)
                    answered = []
                    async with asyncio.timeout(30):
                        while (await client.operation(key))['status'] == 'accepted':
                            for pending in await original.pending_requests(session):
                                response = ({'native_result': {'answers': {'0': 'Two'}}} if pending['kind'] == 'user_input'
                                    else {'outcome': 'selected', 'option_id': 'deny_once' if scenario == 'permission' else 'allow_once'})
                                answer_key = uuid.uuid4().hex
                                await original.respond(pending['request_id'], response, operation_id=answer_key)
                                answered.append(answer_key)
                            await asyncio.sleep(.02)
                    finished = await completion(key)
                    self.assertEqual(finished['status'], 'completed', finished)
                    self.assertEqual(finished['result']['status'], 'completed')
                    if scenario != 'mcp':
                        self.assertTrue(answered, scenario + ' callback was not observed')
                    for answer_key in answered:
                        self.assertEqual((await completion(answer_key))['status'], 'completed')
                self.assertFalse(marker.exists(), 'Denied shell command executed')
                self.assertIn('CAPTURED_MCP:local-report', json.dumps(server.requests[-1]))
                release = threading.Event()
                server.hold_next, server.request_started = release, threading.Event()
                sent = await original.send(session, 'Wait for local cancellation', operation_id='fixture-cancel-send')
                try:
                    self.assertTrue(await asyncio.to_thread(server.request_started.wait, 5))
                    await original.cancel(session, sent['result']['turn_id'], operation_id='fixture-cancel')
                    cancelled = await completion('fixture-cancel-send')
                    self.assertEqual(cancelled['result']['status'], 'cancelled', cancelled)
                    self.assertEqual((await completion('fixture-cancel'))['status'], 'completed')
                finally:
                    release.set()
                count = len(server.requests)
                await original.disconnect(); await original.reconnect()
                await original.send(session, 'Report the local fixture result', operation_id='fixture-send')
                self.assertEqual(len(server.requests), count)
                await launcher.terminate(original.ref['runtime_ref'], operation_id='fixture-stop')
                replacement = await connect()
                restored = await replacement.restore_session(session, launcher=launcher, operation_id='fixture-restore')
                self.assertEqual(restored['status'], 'completed', restored)
                retained = restored['result']['session_ref']
                self.assertEqual(retained['native_session_id'], session['native_session_id'])
                self.assertEqual((await replacement.configuration(retained))['options'][0]['currentValue'], 'gpt-4.1')
            finally:
                for connection in connections:
                    await connection.disconnect()
                for ref in refs:
                    await launcher.terminate(ref['runtime_ref'], operation_id='cleanup:' + ref['connection_id'])
                server.shutdown(); server.server_close()
