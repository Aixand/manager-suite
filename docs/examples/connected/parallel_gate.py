"""Two native fixture processes produce/review an artifact over real Carrier MCP."""
import asyncio
import hashlib
from agent_manager import Client
from connected_runtime import demo, parser, save_json, show
from native_fixture import EXPECTED, adapter, bound, checked_reply, launch_intent, prepare, request, trigger


async def main(args):
    async with demo(args) as app:
        options = prepare(app, ['worker', 'reviewer'])
        async with Client('codex', **options) as agents:
            integration = adapter(agents, app)
            intents = [launch_intent(app, role) for role in ['worker', 'reviewer']]
            launched = await asyncio.gather(*(integration.spawn(**intent) for intent in intents),
                                            return_exceptions=True)
            for actor in launched:
                if not isinstance(actor, BaseException):
                    app.sessions.append(await app.runtime.inspect(actor.session_id))
            for actor in launched:
                if isinstance(actor, BaseException):
                    raise actor
            worker, reviewer = launched
            identities = await asyncio.gather(*(bound(integration, intent) for intent in intents))
            assert worker.session_id != reviewer.session_id
            assert identities[0]['environment_id'] != identities[1]['environment_id']
            assert identities[0]['address'] != identities[1]['address']
            save_json(app.root / 'actors.json', [actor.reference.to_dict() for actor in [worker, reviewer]])
            artifact = app.root / 'report.txt'
            async with app.client('coordinator') as inbox:
                # docs:start acceptance-gate
                for attempt in [1, 2]:
                    task = f'revenue-{attempt}'
                    for role, agent, identity, action in zip(
                            ['worker', 'reviewer'], [worker, reviewer], identities, ['produce', 'review']):
                        message = await request(inbox, app, task + '-' + role, identity['address'],
                            dict(task=task, action=action, artifact=str(artifact), attempt=attempt))
                        await trigger(agent, app, task + '-' + role, dict(request_id=message.id,
                                      sender=app.addresses['coordinator'], task=task))
                        reply = await checked_reply(inbox, message, identity['address'], task)
                        result = reply.json()
                        save_json(app.root / (task + '-' + role + '-accepted.json'), reply.to_dict())
                        await reply.ack()
                        if role == 'worker':
                            worker_digest = result['sha256']
                    data = artifact.read_bytes()
                    digest = hashlib.sha256(data).hexdigest()
                    accepted = (data == EXPECTED and result['status'] == 'accepted'
                                and result['sha256'] == digest == worker_digest)
                    save_json(app.root / (task + '-gate.json'), dict(accepted=accepted, sha256=digest,
                              reviewer=reply.sender, reply_to=reply.reply_to, request_id=message.id))
                    show('gate', attempt=attempt, accepted=accepted, reviewer_status=result['status'])
                    assert accepted == (attempt == 2)
                # docs:end acceptance-gate
            for agent in [worker, reviewer]:
                await agent.stop()
                await agent.wait(lifecycle=('stopped', 'exited', 'failed'), timeout=20)
            await integration.reconcile()
            show('stopped', native_executions=2)


if __name__ == '__main__':
    asyncio.run(main(parser(__doc__).parse_args()))
