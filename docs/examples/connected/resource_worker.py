"""One inbox owner, one task per real process; used by the resource walkthroughs."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from carrier import Client
from prompt_manager import Manager as Prompts
from skill_manager import Manager as Skills, SkillError
from connected_runtime import save_json


def load(path):
    return json.loads(Path(path).read_text())


async def work(args):
    intent = load(args.intent)
    task = intent['payload']
    root = Path(args.intent).parent
    receipt = root / (task['task'] + '-result.json')
    async with Client(url=args.url, credentials=args.credentials) as inbox:
        pending = await inbox.wait(timeout=15, poll_interval=.05)
        assert pending.status == 'messages' and len(pending.messages) == 1
        message = pending.messages[0]
        assert message.sender == intent['sender']
        assert message.recipient == 'demo/worker' and message.reply_to is None
        assert message.idempotency_key == intent['key'] and message.json() == task
        # docs:start acceptance
        reused = receipt.exists()
        if reused:
            accepted = load(receipt)
            assert accepted['intent'] == intent and accepted['message_id'] == message.id
            result = accepted['result']
        else:
            if task['kind'] == 'report':
                package = Prompts(home=root / 'prompts', packs=[]).package('local', 'report')
                captured = package.render('report', revision=task['revision'], format='messages')
                assert captured['output'] == task['prompt']
                assert task['prompt'][0]['content'] == 'Report paid invoice totals in cents.'
                rows = task['invoices']
                result = {'task': task['task'], 'total_cents': sum(r['cents'] for r in rows if r['paid']),
                          'paid_count': sum(r['paid'] for r in rows), 'prompt': task['prompt'],
                          'effect_count': 1, 'executor': 'deterministic local report worker; no model'}
            else:
                project = Skills(root / 'skills-store').project(root / 'project')
                try:
                    operation = (project.connect(task['plan']) if task['kind'] == 'connect'
                                 else project.apply_update(task['plan']))
                    result = {'task': task['task'], 'status': operation['status'],
                              'operation_id': operation['id']}
                except SkillError as error:
                    if error.code != 'conflict' or error.uncertain:
                        raise
                    result = {'task': task['task'], 'status': 'conflict', 'code': error.code}
            save_json(receipt, {'intent': intent, 'message_id': message.id, 'result': result})
        # docs:end acceptance
        save_json(root / (task['task'] + '-worker-' + str(os.getpid()) + '.json'),
                  {'pid': os.getpid(), 'receipt_reused': reused, 'message_id': message.id,
                   'interrupted_before_reply': args.interrupt})
        if args.interrupt:
            # Controlled failure after application acceptance, before reply/ack.
            return 75
        reply_intent = {'payload': result, 'key': task['task'] + '-reply',
                        'to': message.sender, 'reply_to': message.id}
        save_json(root / (task['task'] + '-reply-intent.json'), reply_intent)
        await inbox.send_json(reply_intent['payload'], to=reply_intent['to'],
                              reply_to=reply_intent['reply_to'], idempotency_key=reply_intent['key'])
        await message.ack()
    return 0


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--intent', type=Path, required=True)
    cli.add_argument('--url', required=True)
    cli.add_argument('--credentials', type=Path, required=True)
    cli.add_argument('--interrupt', action='store_true')
    sys.exit(asyncio.run(work(cli.parse_args())))
