"""One real process owns the worker inbox until it exits."""
import asyncio
import json
import sys
from pathlib import Path
from carrier import Client
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from connected_runtime import save_json

async def main(root, interrupt):
    config = json.loads((root / 'worker-config.json').read_text())
    async with Client(url=config['url'], credentials=root / 'credentials/worker.json') as inbox:
        pending = await inbox.wait(timeout=15)
        assert len(pending.messages) == 1
        job = pending.messages[0]
        intent = job.json()
        assert job.sender == 'demo/coordinator' and intent == config['intent']
        # docs:start invoke
        definition = config['definition']
        params = StdioServerParameters(command=definition['command'], args=definition['args'])
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert 'allocate' in [tool.name for tool in tools.tools]
                result = await session.call_tool('allocate', intent)
                assert not result.isError
                value = json.loads(next(item.text for item in result.content if item.type == 'text'))
        assert value == dict(intent, effect_count=1)
        save_json(root / 'accepted-tool-result.json', value)
        if interrupt:
            return  # Deliberate interruption before reply and acknowledgment.
        response = dict(task='inventory-job', result=value)
        save_json(root / 'response-intent.json', dict(body=response, key='inventory-result', reply_to=job.id))
        await job.reply_json(response, idempotency_key='inventory-result')
        await job.ack()
        # docs:end invoke

if __name__ == '__main__':
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2] == 'interrupt'))
