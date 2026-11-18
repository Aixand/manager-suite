"""Recover one native execution after SDK detach, then verify a Carrier reply."""
import asyncio
import json
import os
from undercurrent import Client as Runtime
from agent_manager import Client
from connected_runtime import demo, parser, save_json, show
from native_fixture import adapter, bound, checked_reply, launch_intent, native_state, prepare, request, trigger


async def main(args):
    async with demo(args) as app:
        options = prepare(app, ['worker'])
        intent = launch_intent(app, 'worker')
        # docs:start recover-mailbox
        async with Runtime(**app.runtime.connection_options) as launch_runtime:
            async with Client('codex', **(options | {'runtime': launch_runtime})) as agents:
                integration = adapter(agents, app)
                agent = await integration.spawn(**intent)
                app.sessions.append(await app.runtime.inspect(agent.session_id))
                reference = agent.reference.to_dict()
                save_json(app.root / 'reference.json', reference)
                original = await bound(integration, intent)
                original_pid = (await native_state(app, original['address']))['pid']
        # Closing both SDK connections detaches; the native process stays alive.
        async with Client('codex', **options) as agents:
            integration = adapter(agents, app)
            agent = await agents.get(json.loads((app.root / 'reference.json').read_text()))
            replay = await integration.spawn(**intent)
            assert replay.reference.to_dict() == reference
            assert (await agent.inspect()).lifecycle == 'running'
            os.kill(original_pid, 0)
            show('recovered', same_reference=True, same_native_pid=True, run_id=agent.run_id)
            async with app.client('coordinator') as inbox:
                message = await request(inbox, app, 'echo', original['address'],
                                        dict(task='mailbox-1', action='echo', value='retained process'))
                await trigger(agent, app, 'echo', dict(request_id=message.id,
                              sender=app.addresses['coordinator'], task='mailbox-1'))
                reply = await checked_reply(inbox, message, original['address'], 'mailbox-1')
                assert reply.json()['value'] == 'RETAINED PROCESS'
                save_json(app.root / 'accepted.json', reply.to_dict())
                await reply.ack()
                show('accepted', value=reply.json()['value'], correlated=True, replay_same_message=True)
            await agent.stop()
            stopped = await agent.wait(lifecycle=('stopped', 'exited', 'failed'), timeout=20)
            assert stopped.lifecycle in ('stopped', 'exited', 'failed')
            await integration.reconcile()
            show('stopped', lifecycle=stopped.lifecycle)
        # docs:end recover-mailbox


if __name__ == '__main__':
    asyncio.run(main(parser(__doc__).parse_args()))
