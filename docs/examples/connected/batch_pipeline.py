"""Fan out durable requests to three UC workers and recover one after a real exit."""
import asyncio
import json
from pathlib import Path
import sys

from prompt_manager import Manager
from connected_runtime import demo, parser, save_json, show


async def main(args):
    async with demo(args) as app:
        prompts = Manager(home=app.root / 'prompts', packs=[])
        prompts.create_managed('count', content='Count the nonempty input lines and return their number.', role='user')
        messages = prompts.render_managed('local', 'count', 'count', format='messages')['output']
        jobs = [('alpha', 'one\ntwo\n'), ('beta', 'one\n\n'), ('gamma', 'one\ntwo\nthree\n')]
        async with app.client('coordinator') as coordinator:
            requests, intents, workers = {}, {}, {}
            # docs:start fanout
            for role, content in jobs:
                app.identity(role)
                task = {'job': role, 'text': content, 'messages': messages}
                intent = {'to': app.addresses[role], 'content': json.dumps(task, sort_keys=True), 'idempotency_key': 'count:' + role}
                save_json(app.root / ('intent-' + role + '.json'), intent)
                requests[role] = await coordinator.send(**intent)
                intents[role] = intent
            for role, _ in jobs:
                command = [sys.executable, '-B', Path(__file__).with_name('batch_worker.py'),
                           '--work', app.root, '--url', app.url, '--credentials', app.credentials[role], '--role', role]
                if role == 'alpha':
                    command.append('--crash-after-save')
                workers[role] = await app.start(command, name='count-' + role)
            # docs:end fanout
            first = await app.terminal(workers['alpha'])
            assert first.lifecycle == 'exited' and first.raw['lifecycle']['code'] == 23
            async with app.client('alpha') as stopped_inbox:
                pending = await stopped_inbox.receive_pending()
                assert len(pending.messages) == 1 and pending.messages[0].id == requests['alpha'].id
            show('Interrupted worker', exit_code=23, pending_requests=1, retained_result=True)
            resumed = await app.start([sys.executable, '-B', Path(__file__).with_name('batch_worker.py'),
                '--work', app.root, '--url', app.url, '--credentials', app.credentials['alpha'], '--role', 'alpha'], name='count-alpha-resumed')
            results = []
            for role, _ in jobs:
                answer = await coordinator.wait_for_reply(requests[role], timeout=15)
                result = answer.json()
                assert answer.sender == app.addresses[role] and result['job'] == role
                assert result['lines'] == {'alpha': 2, 'beta': 1, 'gamma': 3}[role]
                save_json(app.root / ('accepted-' + role + '.json'), result)
                await answer.ack()
                repeated = await coordinator.send(**intents[role])
                assert repeated.id == requests[role].id
                results.append(result)
                show('Accepted result', **result)
            for worker in [workers['beta'], workers['gamma'], resumed]:
                final = await app.terminal(worker)
                assert final.lifecycle == 'exited' and final.raw['lifecycle']['code'] == 0
            assert not (await coordinator.receive_pending()).messages
            save_json(app.root / 'batch-result.json', results)
            show('Batch verified', jobs=3, total_lines=6, duplicate_requests=0, original_result_reused=True)


if __name__ == '__main__':
    asyncio.run(main(parser(__doc__).parse_args()))
