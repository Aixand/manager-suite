"""Provider-free denial, explicit queue resume, and idle settings readback."""
import asyncio
import tempfile
from conversation_manager import Client, PreparedLaunch, StandaloneLauncher

async def until(read, predicate):
    async with asyncio.timeout(10):
        while True:
            value = await read()
            if predicate(value):
                return value
            await asyncio.sleep(.02)

async def main():
    with tempfile.TemporaryDirectory(prefix='conversation-controls-') as home:
        client = Client(home)
        launcher = StandaloneLauncher(home,
            service_factory='conversation_manager.fixture_sdk:factory')
        launched = await client.launch(PreparedLaunch(
            backend_id='claude', adapter_id='claude-sdk', protocol='claude-agent-sdk',
            argv=[], cwd=home, transport={'kind': 'sdk'}),
            launcher=launcher, operation_id='fixture-launch')
        assert launched['status'] == 'completed', launched
        ref = launched['result']['connection_ref']
        connection = None
        try:
            connection = await client.connect(ref)
            chat = await connection.create_conversation(operation_id='fixture-create')
            sent = await chat.send('permission', operation_id='permission-turn')
            assert sent['status'] in ('accepted', 'completed'), sent
            turn = sent['result']['turn_id']
            pending = await until(chat.pending_requests, bool)
            request = pending[0]
            assert any(c['optionId'] == 'deny_once' for c in request['choices'])
            queued = await chat.enqueue('follow-up', expected_turn_id=turn,
                                        operation_id='queued-follow-up')
            assert queued['status'] == 'completed', queued
            assert (await chat.queue_state())['paused']
            await chat.respond(request['request_id'],
                {'outcome': 'selected', 'option_id': 'deny_once'}, operation_id='deny')
            await until(chat.state, lambda state: state['active_turn'] is None)
            result = await chat.turn_result(turn)
            assert result['status'] == 'completed', result
            state = await chat.queue_state()
            assert state['paused'] and state['items'][0]['status'] == 'queued', state
            resumed = await chat.resume_queue(operation_id='resume')
            assert resumed['status'] == 'completed', resumed
            state = await until(chat.queue_state,
                                lambda q: q['items'][0]['status'] == 'delivered')
            delivery = state['items'][0]
            await until(chat.state, lambda s: s['active_turn'] is None)
            assert (await chat.turn_result(delivery['turn_id']))['status'] == 'completed'
            evidence = await chat.inspect_queued('queued-follow-up')
            assert evidence['outcome'] == 'completed', evidence
            updated = await chat.set_config('model', 'fixture-two', operation_id='model')
            assert updated['status'] == 'completed', updated
            options = (await chat.configuration())['options']
            assert next(o for o in options if o['id'] == 'model')['currentValue'] == 'fixture-two'
            print('PASS: fixture denial resolved; queue required resume; setting read back')
        finally:
            if connection is not None:
                await connection.disconnect()
            await launcher.terminate(ref['runtime_ref'], operation_id='fixture-stop')

if __name__ == '__main__':
    asyncio.run(asyncio.wait_for(main(), timeout=40))
