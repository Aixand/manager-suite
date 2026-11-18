"""Render a report request, queue it, and verify a real worker's correlated reply."""
import asyncio
import json
from pathlib import Path
import sys

from prompt_manager import Manager
from connected_runtime import demo, parser, save_json, show


async def launch(app, intent, *, interrupt=False):
    argv = [sys.executable, '-B', Path(__file__).with_name('resource_worker.py'),
            '--intent', intent, '--url', app.url, '--credentials', app.credentials['worker']]
    if interrupt:
        argv.append('--interrupt')
    session = await app.start(argv, name='report-worker-interrupted' if interrupt else 'report-worker-recovered')
    terminal = await app.terminal(session)
    save_json(app.root / ('interrupted-session.json' if interrupt else 'worker-session.json'),
              terminal.raw)
    output = await app.runtime.read_output(session, max_bytes=8192)
    (app.root / ('interrupted-worker.log' if interrupt else 'worker.log')).write_bytes(output.data)
    assert terminal.lifecycle == 'exited' and terminal.raw['lifecycle']['code'] == (75 if interrupt else 0)
    return session


async def run(args):
    async with demo(args) as app:
        # docs:start report-request
        manager = Manager(home=app.root / 'prompts', packs=[])
        created = manager.create_managed('report', content='Report paid invoice totals in cents.')
        package = manager.package('local', 'report')
        rendered = package.render('report', revision=created['revision'], format='messages')
        payload = {'task': 'paid-report', 'kind': 'report', 'revision': created['revision'],
                   'prompt': rendered['output'], 'invoices': [
                       {'cents': 12500, 'paid': True}, {'cents': 2750, 'paid': True},
                       {'cents': 9000, 'paid': False}]}
        intent = {'sender': app.addresses['coordinator'], 'to': app.addresses['worker'],
                  'key': 'paid-report-request', 'payload': payload}
        save_json(app.root / 'delivery-intent.json', intent)
        async with app.client('coordinator') as inbox:
            request = await inbox.send_json(payload, to=intent['to'], idempotency_key=intent['key'])
            await launch(app, app.root / 'delivery-intent.json')
            reply = await inbox.wait_for_reply(request, timeout=10, poll_interval=.05)
            assert reply.sender == intent['to'] and reply.reply_to == request.id
            result = reply.json()
            assert result['task'] == payload['task'] and result['total_cents'] == 15250
            assert result['paid_count'] == 2 and result['prompt'] == rendered['output']
            save_json(app.root / 'accepted-reply.json', reply.to_dict())
            await reply.ack()
            assert (await inbox.delivery_status(intent['key']))['status'] == 'acknowledged'
        # docs:end report-request
        disk = json.loads((app.root / 'paid-report-result.json').read_text())
        assert disk['result'] == result and disk['message_id'] == request.id
        show('Report verified', total_cents=result['total_cents'], paid_count=result['paid_count'],
             correlated=True, request_acknowledged=True)


if __name__ == '__main__':
    asyncio.run(run(parser(__doc__).parse_args()))
