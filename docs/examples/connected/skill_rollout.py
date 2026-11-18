"""Deliver a reviewed configuration skill, reject project edits, then explicitly update."""
import asyncio
import json
from pathlib import Path
import sys
from skill_manager import Manager, FileContent
from connected_runtime import demo, parser, save_json, show


async def deliver(app, task, kind, plan, *, role='coordinator'):
    intent = {'sender': app.addresses[role], 'to': app.addresses['worker'],
              'key': task + '-request', 'payload': {'task': task, 'kind': kind, 'plan': plan}}
    path = app.root / (task + '-intent.json')
    save_json(path, intent)
    async with app.client(role) as inbox:
        request = await inbox.send_json(intent['payload'], to=intent['to'], idempotency_key=intent['key'])
        session = await app.start([sys.executable, '-B', Path(__file__).with_name('resource_worker.py'),
                                  '--intent', path, '--url', app.url,
                                  '--credentials', app.credentials['worker']], name=task)
        observed = await app.terminal(session)
        save_json(app.root / (task + '-session.json'), observed.raw)
        output = await app.runtime.read_output(session, max_bytes=8192)
        (app.root / (task + '-worker.log')).write_bytes(output.data)
        assert observed.lifecycle == 'exited' and observed.raw['lifecycle']['code'] == 0
        reply = await inbox.wait_for_reply(request, timeout=10, poll_interval=.05)
        assert reply.sender == intent['to'] and reply.reply_to == request.id
        result = reply.json()
        assert result['task'] == task
        save_json(app.root / (task + '-accepted.json'), reply.to_dict())
        await reply.ack()
        assert (await inbox.delivery_status(intent['key']))['status'] == 'acknowledged'
        return result


async def run(args):
    async with demo(args) as app:
        source = app.root / 'publisher' / 'invoice-policy'
        source.mkdir(parents=True)
        (source / 'SKILL.md').write_text('---\nname: invoice-policy\ndescription: Configure invoice reporting.\n---\nUse config.json for report policy.\n')
        old = b'{"include_unpaid": false, "currency": "PHP"}\n'
        new = b'{"include_unpaid": false, "currency": "PHP", "minimum_cents": 1000}\n'
        (source / 'config.json').write_bytes(old)
        manager = Manager(app.root / 'skills-store')
        skill = manager.skill('finance', 'invoice-policy')
        captured = skill.register(source, publisher_revision=1).content
        project_root = app.root / 'project'
        project_root.mkdir()
        project = manager.project(project_root)
        plan = project.preview_connect([skill.reference], skills_dir='skills')
        target = project_root / 'skills' / 'invoice-policy' / 'config.json'
        assert not target.exists()
        installed = await deliver(app, 'install-policy', 'connect', plan)
        assert installed['status'] == 'completed' and target.read_bytes() == old
        # docs:start reviewed-rollout
        skill.save({'config.json': FileContent(new)}, expected_revision=captured.revision)
        pending = project.preview_update()
        save_json(app.root / 'reviewed-plan-before-edit.json', pending)
        local = b'{"include_unpaid": true, "currency": "PHP"}\n'
        target.write_bytes(local)
        rejected = await deliver(app, 'stale-update', 'update', pending)
        assert rejected['status'] == 'conflict' and target.read_bytes() == local
        # This deterministic reviewer fixture explicitly preserves and sets aside the edit.
        (app.root / 'retained-project-config.json').write_bytes(target.read_bytes())
        save_json(app.root / 'review-decision.json', {
            'actor': 'local scripted reviewer; no model or human approval claimed',
            'decision': 'Preserve local edit; restore installed base; apply accepted minimum threshold',
            'old': old.decode(), 'local': local.decode(), 'new': new.decode()})
        target.write_bytes(old)
        reviewed = project.preview_update()
        save_json(app.root / 'reviewed-update-plan.json', reviewed)
        assert target.read_bytes() == old
        updated = await deliver(app, 'reviewed-update', 'update', reviewed, role='reviewer')
        assert updated['status'] == 'completed' and target.read_bytes() == new
        repeated = project.apply_update(reviewed)
        assert repeated['id'] == updated['operation_id'] and repeated['status'] == 'completed'
        assert (app.root / 'retained-project-config.json').read_bytes() == local
        # docs:end reviewed-rollout
        save_json(app.root / 'project-operations.json', project.operations())
        evidence = {'conflict_preserved_edit': True, 'reviewed_update': updated['status'],
                    'same_operation_on_replay': repeated['id'] == updated['operation_id'],
                    'installed_config': json.loads(target.read_text()), 'local_edit_retained': True}
        save_json(app.root / 'rollout-verification.json', evidence)
        show('Skill rollout verified', **evidence)


if __name__ == '__main__':
    asyncio.run(run(parser(__doc__).parse_args()))
