"""One inbox owner handles one batch item; a saved result survives an injected crash."""
import argparse
import asyncio
import json
from pathlib import Path

from carrier import Client
from connected_runtime import save_json


async def main(args):
    journal = args.work / ('result-' + args.role + '.json')
    async with Client(url=args.url, credentials=args.credentials) as inbox:
        page = await inbox.wait(timeout=15, limit=1)
        assert page.status == 'messages'
        request = page.messages[0]
        task = request.json()
        assert request.sender == 'demo/coordinator'
        assert task['messages'][0]['content'] == 'Count the nonempty input lines and return their number.'
        # docs:start recover-worker
        if journal.exists():
            retained = json.loads(journal.read_text())
            assert retained['request_id'] == request.id and retained['task'] == task
            result = retained['result']
        else:
            result = {'job': task['job'], 'lines': sum(bool(line.strip()) for line in task['text'].splitlines())}
            save_json(journal, {'request_id': request.id, 'task': task, 'result': result})
        if args.crash_after_save:
            raise SystemExit(23)  # A deliberate failure after durable acceptance.
        await request.reply_json(result, idempotency_key='batch-result:' + str(request.id))
        await request.ack()
        # docs:end recover-worker


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--work', type=Path, required=True)
    cli.add_argument('--url', required=True)
    cli.add_argument('--credentials', required=True)
    cli.add_argument('--role', required=True)
    cli.add_argument('--crash-after-save', action='store_true')
    asyncio.run(main(cli.parse_args()))
