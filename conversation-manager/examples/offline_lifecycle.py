"""Read retained local evidence after the built-in fixture worker has stopped."""
import asyncio
import tempfile
from conversation_manager import Client
from conversation_manager.demo import demo

async def main():
    with tempfile.TemporaryDirectory(prefix='conversation-recorded-') as home:
        result = await demo(home=home)
        assert result['status'] == 'completed', result
        client = Client(home)
        reference = result['conversation_ref']
        runtime = result['connection_ref']['runtime_ref']
        assert (await client.inspect_runtime(runtime))['state'] == 'stopped'
        turn_id = result['send_receipt']['result']['turn_id']
        # These reads use the local journal, without attaching or starting a worker.
        history = await client.history(reference)
        assert history['session_ref'] == reference
        assert history['entries']
        terminal = await client.turn_result(reference, turn_id)
        assert terminal['status'] == 'completed', terminal
        assert any(row['session_id'] == reference['session_id']
                   for row in await client.conversations())
        assert (await client.inspect_runtime(runtime))['state'] == 'stopped'
        print('PASS: stopped fixture worker; retained history and exact turn read without attach')

if __name__ == '__main__':
    asyncio.run(asyncio.wait_for(main(), timeout=40))
