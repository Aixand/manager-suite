"""Offline Codex protocol fixture for the real managed launch chain on every OS."""
import asyncio
from http import HTTPStatus
import json
import os
from pathlib import Path
import secrets
import sys
from urllib.parse import urlsplit
import uuid


async def main():
    home = Path(os.environ['CODEX_HOME'])
    threads = {}
    models = [dict(id=model, model=model, displayName=model, isDefault=index == 0,
        defaultReasoningEffort='medium', supportedReasoningEfforts=[{'reasoningEffort': value}
            for value in (('medium',) if index == 0 else ('medium', 'high'))])
        for index, model in enumerate(('fixture-one', 'fixture-two'))]

    if sys.argv[1:] == ['--version']:
        print('0.162.1')
        return
    target = sys.argv[sys.argv.index('--listen') + 1]
    if target == 'stdio://':
        # Shared metadata discovery uses this disposable protocol without a
        # managed conversation or socket. The runtime fixture below is separate.
        for line in sys.stdin:
            message = json.loads(line)
            method = message['method']
            assert method in ('initialize', 'initialized', 'model/list')
            if 'id' not in message: continue
            result = {'data': models} if method == 'model/list' else {'serverInfo': {'version': '0.162.1'}}
            print(json.dumps({'id': message['id'], 'result': result}), flush=True)
        return

    async def peer(socket):
        async for raw in socket:
            message = json.loads(raw)
            if 'id' not in message or 'method' not in message:
                continue
            method, params = message['method'], message.get('params') or {}
            if method == 'initialize':
                result = {'serverInfo': {'version': '0.162.1'}}
            elif method == 'model/list':
                result = {'data': models}
            elif method == 'config/read':
                result = {'config': {}, 'origins': {}}
            elif method == 'permissionProfile/list':
                result = {'data': [{'id': ':read-only', 'allowed': True}]}
            elif method == 'thread/start':
                identifier = str(uuid.uuid4())
                settings = dict(model=params.get('model', 'fixture-one'), effort='medium', cwd=params['cwd'])
                threads[identifier] = settings
                (home / 'threads.json').write_text(json.dumps(threads))
                result = {'thread': {'id': identifier, 'cwd': params['cwd'], 'turns': []}, **settings}
            elif method == 'thread/settings/update':
                settings = threads[params['threadId']]
                settings.update({key: value for key, value in params.items() if key != 'threadId'})
                result = {}
                await socket.send(json.dumps({'method': 'thread/settings/updated', 'params': {
                    'threadId': params['threadId'], 'threadSettings': settings}}))
            elif method == 'turn/start':
                (home / 'first-turn.json').write_text(json.dumps(threads[params['threadId']]))
                result = {'turn': {'id': str(uuid.uuid4()), 'status': 'completed'}}
            elif method == 'thread/loaded/list':
                result = {'data': list(threads)}
            else:
                await socket.send(json.dumps({'id': message['id'], 'error': {'code': -32601, 'message': 'Unsupported fixture method: ' + method}}))
                continue
            await socket.send(json.dumps({'id': message['id'], 'result': result}))

    if target.startswith('unix://'):
        from websockets.asyncio.server import unix_serve
        server = await unix_serve(peer, target.removeprefix('unix://'))
    else:
        from websockets.asyncio.server import serve
        assert sys.argv[sys.argv.index('--ws-auth') + 1] == 'capability-token'
        token = Path(sys.argv[sys.argv.index('--ws-token-file') + 1]).read_text()

        def authenticate(connection, request):
            if not secrets.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + token):
                return connection.respond(HTTPStatus.UNAUTHORIZED, 'Authentication required\n')

        address = urlsplit(target)
        server = await serve(peer, address.hostname, address.port, process_request=authenticate)
    async with server:
        await asyncio.Future()


if __name__ == '__main__':
    asyncio.run(main())
