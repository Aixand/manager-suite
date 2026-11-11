"""Offline executable fixture for Agent Manager -> retained Codex conversations."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import uuid


async def main():
    endpoint = sys.argv[sys.argv.index('--listen') + 1]
    home = Path(os.environ['CODEX_HOME'])
    with (home / 'launches.jsonl').open('a') as log:
        log.write(json.dumps(dict(pid=os.getpid(), home=str(home),
            ambient_key='OPENAI_API_KEY' in os.environ)) + '\n')
    database = home / "fixture-threads.json"
    sessions = json.loads(database.read_text()) if database.exists() else {}
    tasks = set()

    async def turn(socket, thread, identifier, text):
        await asyncio.sleep(.2)
        await socket.send(json.dumps(dict(method='item/agentMessage/delta', params=dict(
            threadId=thread, turnId=identifier, itemId='reply', delta='fixture:' + text))))
        await socket.send(json.dumps(dict(method='turn/completed', params=dict(
            threadId=thread, turn=dict(id=identifier, status='completed')))))

    async def peer(socket):
        async for raw in socket:
            request = json.loads(raw)
            method = request.get('method')
            params = request.get('params', {})
            if 'id' not in request:
                continue
            if method == 'initialize':
                result = {'serverInfo': {'version': '0.166.0'}}
            elif method == 'account/read':
                result = dict(account=None if (home / 'signed-out').exists() else {'type': 'chatgpt'}, requiresOpenaiAuth=True)
            elif method == 'thread/start':
                with (home / 'creations.jsonl').open('a') as log:
                    log.write(json.dumps({'id': request['id']}) + '\n')
                if (home / 'reject-create').exists():
                    await socket.send(json.dumps(dict(id=request['id'], error=dict(code=-32602, message='Fixture rejected creation'))))
                    continue
                if (home / 'disconnect-create').exists():
                    await socket.close()
                    continue
                if (home / 'slow-create').exists():
                    await asyncio.sleep(.3)
                thread = dict(id=str(uuid.uuid4()), **params)
                sessions[thread['id']] = thread
                database.write_text(json.dumps(sessions))
                result = dict(thread=thread)
            elif method in ('thread/resume', 'thread/read'):
                result = dict(thread=sessions[params['threadId']])
            elif method == 'turn/start':
                with (home / 'turns.jsonl').open('a') as log:
                    log.write(json.dumps(params) + '\n')
                identifier = str(uuid.uuid4())
                result = dict(turn=dict(id=identifier, status='inProgress'))
                task = asyncio.create_task(turn(socket, params['threadId'], identifier,
                    ''.join(part['text'] for part in params['input'])))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            else:
                result = {}
            await socket.send(json.dumps(dict(id=request['id'], result=result)))

    if endpoint == 'stdio://':
        class Stdio:
            closed = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self.closed:
                    raise StopAsyncIteration
                raw = await asyncio.to_thread(sys.stdin.buffer.readline)
                if not raw:
                    raise StopAsyncIteration
                return raw.decode('utf-8')

            async def send(self, value):
                sys.stdout.buffer.write(value.encode('utf-8') + b'\n')
                sys.stdout.buffer.flush()

            async def close(self):
                self.closed = True
        await peer(Stdio())
    else:
        from websockets.asyncio.server import unix_serve
        done = asyncio.Event()
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, done.set)
        async with unix_serve(peer, endpoint.removeprefix('unix://'), compression=None):
            await done.wait()
    await asyncio.gather(*tasks, return_exceptions=True)


if '--version' in sys.argv:
    print('codex-cli 0.166.0')
else:
    asyncio.run(main())
