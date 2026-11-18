"""Keep queued prompt bytes stable across an edit and a worker interruption."""
import asyncio
import json
from prompt_manager import Manager
from connected_runtime import demo, parser, save_json, show
from prompt_worker import launch


async def run(args):
    async with demo(args) as app:
        manager = Manager(home=app.root / 'prompts', packs=[])
        created = manager.create_managed('report', content='Report paid invoice totals in cents.')
        package = manager.package('local', 'report')
        # docs:start frozen-delivery
        old = package.render('report', revision=created['revision'], format='messages')['output']
        payload = {'task': 'frozen-report', 'kind': 'report', 'revision': created['revision'],
                   'prompt': old, 'invoices': [{'cents': 12500, 'paid': True},
                                             {'cents': 2750, 'paid': True}]}
        intent = {'sender': app.addresses['coordinator'], 'to': app.addresses['worker'],
                  'key': 'frozen-report-request', 'payload': payload}
        path = app.root / 'delivery-intent.json'
        save_json(path, intent)
        async with app.client('coordinator') as inbox:
            request = await inbox.send_json(payload, to=intent['to'], idempotency_key=intent['key'])
            package.save({'body.md': 'Report all invoices, including unpaid invoices.'},
                         expected_revision=created['revision'])
            new = package.render('report', format='messages')['output']
            assert old != new
            await launch(app, path, interrupt=True)
            accepted_path = app.root / 'frozen-report-result.json'
            first_bytes = accepted_path.read_bytes()
            first_stat = accepted_path.stat()
            assert (await inbox.delivery_status(intent['key']))['status'] == 'stored'
            assert not (await inbox.receive_pending()).messages
            replay = await inbox.send_json(**{'content': payload, 'to': intent['to'],
                                             'idempotency_key': intent['key']})
            assert replay.id == request.id
            await launch(app, path)
            reply = await inbox.wait_for_reply(request, timeout=10, poll_interval=.05)
            assert reply.sender == intent['to'] and reply.reply_to == request.id
            result = reply.json()
            assert result['task'] == payload['task'] and result['prompt'] == old
            assert result['total_cents'] == 15250 and result['effect_count'] == 1
            assert accepted_path.read_bytes() == first_bytes
            assert accepted_path.stat().st_mtime_ns == first_stat.st_mtime_ns
            observations = [json.loads(p.read_text()) for p in app.root.glob('frozen-report-worker-*.json')]
            assert len(observations) == 2 and len({r['pid'] for r in observations}) == 2
            assert sorted(r['receipt_reused'] for r in observations) == [False, True]
            save_json(app.root / 'accepted-reply.json', reply.to_dict())
            await reply.ack()
            assert (await inbox.delivery_status(intent['key']))['status'] == 'acknowledged'
        # docs:end frozen-delivery
        evidence = {'old': old[0]['content'], 'new': new[0]['content'], 'same_message': replay.id == request.id,
                    'result_unchanged': accepted_path.read_bytes() == first_bytes, 'total_cents': result['total_cents']}
        save_json(app.root / 'frozen-verification.json', evidence)
        show('Frozen report verified', **evidence)


if __name__ == '__main__':
    asyncio.run(run(parser(__doc__).parse_args()))
