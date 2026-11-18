"""Receive one captured audit task, run its selected skill under UC, and reply."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

from carrier import Client
from undercurrent import Client as Runtime
from connected_runtime import save_json


async def main(args):
    root = args.work.resolve()
    run = json.loads((root / 'run.json').read_text())
    async with Runtime(home=run['runtime_home'], bridge=str(Path(run['runtime_bin']) / 'uc-python-bridge')) as runtime:
        async with Client(url=args.url, credentials=args.credentials) as worker:
            page = await worker.wait(timeout=15, limit=1)
            assert page.status == 'messages'
            request = page.messages[0]
            task = request.json()
            assert task['task_id'] == 'contacts-001' and request.sender == 'demo/coordinator'
            script = (root / 'project/skills/contact-audit/scripts/audit.py').resolve()
            source = root / 'project/contacts.csv'
            output = root / 'project/audit.json'
            assert hashlib.sha256(script.read_bytes()).hexdigest() == task['script_sha256']
            assert hashlib.sha256(source.read_bytes()).hexdigest() == task['input_sha256']
            save_json(root / 'execution-intent.json', {'name': 'contact-audit', 'task_id': task['task_id']})
            # docs:start execute-audit
            session = await runtime.start(
                [sys.executable, str(script), str(source), str(output)],
                name='contact-audit', cwd=str(root / 'project'), detection=False,
            )
            save_json(root / 'execution.json', {'session_id': session.id})
            final = await runtime.wait(session, lifecycle={'exited', 'failed', 'stopped'}, timeout=15)
            assert final.lifecycle == 'exited' and final.raw['lifecycle']['code'] == 0
            report = json.loads(output.read_text())
            assert report == {'rows': 4, 'missing_email_records': [3, 5], 'status': 'needs-review'}
            digest = hashlib.sha256(output.read_bytes()).hexdigest()
            result = {'task_id': task['task_id'], 'report': report, 'artifact_sha256': digest,
                      'session_id': session.id}
            save_json(root / 'result-before-reply.json', result)
            await request.reply_json(result, idempotency_key='audit-result:' + str(request.id))
            await request.ack()
            # docs:end execute-audit


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--url', required=True)
    parser.add_argument('--credentials', required=True)
    asyncio.run(main(parser.parse_args()))
