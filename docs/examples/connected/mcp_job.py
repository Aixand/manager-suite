"""Resolve, invoke, interrupt and replay a real MCP job over Carrier and Undercurrent."""
import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from mcp_manager import Manager
from connected_runtime import demo, parser, save_json, show

async def main(args):
    async with demo(args) as app:
        folder = Path(__file__).resolve().parent
        manager = Manager(home=app.root / 'mcp-manager')
        manager.add('inventory', {'command': sys.executable,
            'args': [str(folder / 'tool_server.py'), str(app.root / 'inventory.db')]})
        resolved = manager.resolve(['inventory'], str(app.root), project=None, check=False)
        intent = {'operation': 'allocation-001', 'units': 3}
        save_json(app.root / 'job-intent.json', dict(body=intent, key='inventory-job', to=app.addresses['worker']))
        save_json(app.root / 'worker-config.json', dict(url=app.url, intent=intent,
            definition=resolved['servers']['inventory']))
        async with app.client('coordinator') as inbox:
            # docs:start recovery
            original = await inbox.send_json(intent, to=app.addresses['worker'], idempotency_key='inventory-job')
            first = await app.start([sys.executable, folder / 'mcp_worker.py', app.root, 'interrupt'], name='mcp-first')
            assert (await app.terminal(first)).lifecycle == 'exited'
            assert json.loads((app.root / 'accepted-tool-result.json').read_text())['effect_count'] == 1
            replay = await inbox.send_json(intent, to=app.addresses['worker'], idempotency_key='inventory-job')
            assert replay.id == original.id
            second = await app.start([sys.executable, folder / 'mcp_worker.py', app.root, 'finish'], name='mcp-recovery')
            answer = await inbox.wait_for_reply(original, timeout=20)
            assert answer.sender == app.addresses['worker'] and answer.reply_to == original.id
            assert answer.json() == {'task': 'inventory-job', 'result': dict(intent, effect_count=1)}
            save_json(app.root / 'verified.json', answer.json())
            await answer.ack()
            assert (await app.terminal(second)).lifecycle == 'exited'
            with sqlite3.connect(app.root / 'inventory.db') as db:
                assert db.execute('SELECT operation, units FROM allocations').fetchall() == [('allocation-001', 3)]
            assert (await inbox.delivery_status('inventory-job'))['status'] == 'acknowledged'
            show('MCP verified', effect_count=1, replay_same_message=True, worker_processes=2, units=3)
            # docs:end recovery

if __name__ == '__main__':
    asyncio.run(main(parser(__doc__).parse_args()))
