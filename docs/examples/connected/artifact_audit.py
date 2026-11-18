"""Compose Prompt + Skill Manager, UC execution and durable Carrier artifact delivery."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys

from prompt_manager import Manager as Prompts
from skill_manager import Manager as Skills
from connected_runtime import demo, parser, save_json, show

AUDITOR = '''import csv,json,sys
from pathlib import Path
with open(sys.argv[1],newline="",encoding="utf-8") as stream:
    rows=list(csv.DictReader(stream))
report={"rows":len(rows),"missing_email_records":[i for i,row in enumerate(rows,2) if not row["email"].strip()]}
report["status"]="needs-review" if report["missing_email_records"] else "passed"
Path(sys.argv[2]).write_text(json.dumps(report,sort_keys=True)+"\\n")
'''


async def main(args):
    async with demo(args) as app:
        prompts = Prompts(home=app.root / 'prompts', packs=[])
        prompts.create_managed('audit', content='Audit the contacts CSV for missing email addresses; report source record numbers.', role='user')
        rendered = prompts.render_managed('local', 'audit', 'audit', format='messages')
        save_json(app.root / 'captured-prompt.json', rendered['output'])
        publisher = app.root / 'publisher/contact-audit'
        (publisher / 'scripts').mkdir(parents=True)
        (publisher / 'SKILL.md').write_text('---\nname: contact-audit\ndescription: Check contacts for missing email addresses.\n---\nRun scripts/audit.py with input CSV and output JSON paths.\n')
        (publisher / 'scripts/audit.py').write_text(AUDITOR)
        skills = Skills(app.root / 'skills')
        skill = skills.skill('contacts-app', 'audit')
        skill.register(publisher, publisher_revision=1)
        project_root = app.root / 'project'
        project_root.mkdir()
        project = skills.project(project_root)
        # docs:start capture-and-deliver
        plan = project.preview_connect([skill.reference], skills_dir='skills')
        assert not (project_root / 'skills').exists()
        project.connect(plan)
        script = project_root / 'skills/contact-audit/scripts/audit.py'
        source = project_root / 'contacts.csv'
        source.write_text('name,email\nAda,ada@example.test\nLin,\nSam,sam@example.test\nJo,\n')
        task = {'task_id': 'contacts-001', 'messages': rendered['output'],
                'script_sha256': hashlib.sha256(script.read_bytes()).hexdigest(),
                'input_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
        intent = {'to': app.addresses['worker'], 'content': json.dumps(task, sort_keys=True),
                  'idempotency_key': 'contacts:001'}
        save_json(app.root / 'delivery.json', intent)
        async with app.client('coordinator') as inbox:
            sent = await inbox.send(**intent)
            show('Queued audit', task_id=task['task_id'], message_id=sent.id, worker_running=False)
            worker = await app.start([sys.executable, '-B', Path(__file__).with_name('artifact_worker.py'),
                '--work', app.root, '--url', app.url, '--credentials', app.credentials['worker']], name='audit-worker')
            answer = await inbox.wait_for_reply(sent, timeout=20)
            result = answer.json()
            assert answer.sender == app.addresses['worker'] and result['task_id'] == task['task_id']
            assert hashlib.sha256((project_root / 'audit.json').read_bytes()).hexdigest() == result['artifact_sha256']
            save_json(app.root / 'acceptance.json', result)
            await answer.ack()
            # docs:end capture-and-deliver
            finished = await app.terminal(worker)
            assert finished.lifecycle == 'exited' and finished.raw['lifecycle']['code'] == 0
            repeated = await inbox.send(**intent)
            assert repeated.id == sent.id
            assert (await inbox.delivery_status(intent['idempotency_key']))['status'] == 'acknowledged'
            assert not (await inbox.receive_pending(reply_to=sent.id)).messages
            show('Checked artifact', **result['report'], digest_matches=True)
            show('Replay', original_message=True, pending_replies=0, reran_auditor=False)
        show('Evidence', files=['captured-prompt.json', 'delivery.json', 'execution.json', 'project/audit.json', 'acceptance.json'])


if __name__ == '__main__':
    asyncio.run(main(parser(__doc__).parse_args()))
