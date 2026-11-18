"""Carrier task to retained Conversation Manager worker owned by Undercurrent.

The explicitly injected provider is conversation_manager.fixture_sdk, not Claude.
The approval allows a fixture event; it does not write the advertised file.
"""
import asyncio
from conversation_manager import Client, PreparedLaunch, WorkerLauncher
from connected_runtime import demo, parser, save_json, show, until

class Owner:
    """Implement the public ProcessOwner contract using exact tracked UC sessions."""
    def __init__(self, app):
        self.app, self.sessions = app, {}

    async def start(self, command, *, cwd, operation_id):
        session = await self.app.start(command, name='retained-conversation')
        self.sessions[session.id] = session
        return {'owner': 'connected-undercurrent', 'id': session.id}

    async def inspect(self, ref):
        assert ref['owner'] == 'connected-undercurrent' and ref['id'] in self.sessions
        state = await self.app.runtime.inspect(self.sessions[ref['id']])
        return {'runtime_ref': ref, 'state': state.lifecycle}

    async def stop(self, ref, *, operation_id):
        await self.inspect(ref)
        await self.app.stop(self.sessions[ref['id']])

async def main(args):
    async with demo(args) as app:
        owner = Owner(app)
        client = Client(app.root / 'conversation-manager')
        launcher = WorkerLauncher(owner, client.home,
            service_factory='conversation_manager.fixture_sdk:factory')
        launch = await client.launch(PreparedLaunch(backend_id='claude', adapter_id='claude-sdk',
            protocol='claude-agent-sdk', argv=[], cwd=str(app.root), transport={'kind': 'sdk'}),
            launcher=launcher, operation_id='retained-launch')
        assert launch['status'] == 'completed', launch
        ref = launch['result']['connection_ref']
        save_json(app.root / 'connection.json', ref)
        connection = await client.connect(ref)
        try:
            chat = await connection.create_conversation(operation_id='inventory-review-create')
            save_json(app.root / 'conversation.json', chat.reference)
            task = {'task': 'inventory-review', 'prompt': 'permission', 'operation': 'inventory-review-turn'}
            save_json(app.root / 'task-intent.json', dict(body=task, key='review-job', to=app.addresses['worker']))
            async with app.client('coordinator') as coordinator, app.client('worker') as worker:
                request = await coordinator.send_json(task, to=app.addresses['worker'], idempotency_key='review-job')
                received = await worker.wait(timeout=10)
                assert len(received.messages) == 1
                job = received.messages[0]
                assert job.id == request.id and job.sender == app.addresses['coordinator'] and job.json() == task
                save_json(app.root / 'accepted-task.json', task)
                await job.ack()
                # docs:start retained
                sent = await chat.send(task['prompt'], operation_id=task['operation'])
                assert sent['status'] in {'accepted', 'completed'}, sent
                turn = sent['result']['turn_id']
                await chat.disconnect()
                assert (await owner.inspect(ref['runtime_ref']))['state'] == 'running'
                await chat.reconnect()
                assert chat.connection.ref == ref
                pending = await until(chat.pending_requests, bool)
                assert len(pending) == 1
                approval = {'outcome': 'selected', 'option_id': 'allow_once'}
                save_json(app.root / 'approval-intent.json', dict(request_id=pending[0]['request_id'],
                    response=approval, operation_id='fixture-approve-once'))
                await chat.respond(pending[0]['request_id'], approval, operation_id='fixture-approve-once')
                async def result():
                    return await chat.turn_result(turn)
                completed = await until(result, lambda row: row.get('status') == 'completed')
                assert completed['final_text'] == 'approved', completed
                assert completed['turn_id'] == turn and completed['operation_id'] == task['operation']
                assert completed['truncated'] is False
                save_json(app.root / 'turn-result.json', completed)
                response = {'task': task['task'], 'operation': task['operation'], 'turn_id': turn,
                    'final_text': completed['final_text'], 'runtime_ref': ref['runtime_ref']}
                save_json(app.root / 'response-intent.json', dict(body=response, key='review-result', reply_to=job.id))
                await job.reply_json(response, idempotency_key='review-result')
                answer = await coordinator.wait_for_reply(request)
                assert answer.sender == app.addresses['worker'] and answer.reply_to == request.id
                assert answer.json() == response
                save_json(app.root / 'verified.json', answer.json())
                await answer.ack()
                # docs:end retained
                show('Conversation verified', provider='local-sdk-fixture', final_text='approved',
                    reconnected_same_runtime=True, owned_workers=len(owner.sessions))
        finally:
            await connection.disconnect()
            await launcher.terminate(ref['runtime_ref'], operation_id='retained-stop')
            observed = await owner.inspect(ref['runtime_ref'])
            assert observed['state'] in {'exited', 'stopped', 'failed'}, observed
            save_json(app.root / 'owner-stopped.json', observed)

if __name__ == '__main__':
    asyncio.run(main(parser(__doc__).parse_args()))
