"""Local reference chat. All agent operations use public SDK interfaces."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import uuid
from urllib.parse import urlsplit, parse_qs

from conversation_manager import ConversationClient, ConversationError, PreparedLaunch, StandaloneLauncher

ROOT = Path(__file__).resolve().parent


class App:
    def __init__(self, args):
        self.args = args
        self.data = Path(args.state).resolve()
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.work = self.data / 'demo-work'
        self.work.mkdir(exist_ok=True)
        self.client = ConversationClient(self.data / 'sdk', agent_config=args.agent_config, agent_root=args.agent_root)
        self.launcher = StandaloneLauncher(self.client.home, service_factory='conversation_manager.fixture_sdk:factory')
        self.path = self.data / 'ui.json'
        self.meta = json.loads(self.path.read_text()) if self.path.exists() else {'chats': {}, 'starts': []}
        self.handles = {}
        self.detached = set()
        self.agents = {}
        self.started = set()

    def save(self):
        temporary = self.path.with_suffix('.new')
        temporary.write_text(json.dumps(self.meta, indent=2))
        temporary.chmod(0o600)
        temporary.replace(self.path)

    async def agents_list(self):
        self.agents = {'demo': {'key': 'demo', 'label': 'Offline SDK demo', 'provider': 'demo', 'supported': True}}
        error = None
        if self.args.live:
            try:
                for row in await self.client.available_agents():
                    target = row['target']
                    key = target['provider'] + ':' + target['environment_id']
                    labels = ', '.join(target.get('aliases') or []) or target['environment_id'][:8]
                    self.agents[key] = dict(key=key, label=target['provider'].capitalize() + ' · ' + labels,
                        provider=target['provider'], supported=row['conversation']['supported'], reference=row['reference'])
            except Exception as exc:
                error = str(exc)
        return {'agents': [{k: v for k, v in row.items() if k != 'reference'} for row in self.agents.values()],
                'live_enabled': self.args.live, 'discovery_error': error, 'demo_workspace': str(self.work)}

    async def get(self, identifier):
        if identifier not in self.meta['chats']:
            raise ConversationError('not_found', 'Conversation not found')
        if self.meta['chats'][identifier].get('backend_stopped'):
            raise ConversationError('closed', 'Backend was stopped. Recorded history remains available.')
        if identifier in self.detached:
            raise ConversationError('conflict', 'Frontend detached. Reconnect to continue.')
        if identifier not in self.handles:
            self.handles[identifier] = await self.client.attach_conversation(self.meta['chats'][identifier]['reference'])
        return self.handles[identifier]

    async def start(self, body):
        if not self.agents:
            await self.agents_list()
        agent = self.agents.get(body.get('agent', 'demo'))
        if not agent or not agent['supported']:
            raise ConversationError('unsupported', 'Select a supported agent')
        identifier = uuid.UUID(body['start_id']).hex
        operation = 'start:' + identifier
        intent = {key: body.get(key) for key in ('agent', 'cwd', 'model')}
        previous = next((item for item in self.meta['starts'] if item['operation'] == operation), None)
        if previous and previous.get('intent') != intent:
            raise ConversationError('conflict', 'Startup ID already names a different conversation')
        if identifier in self.meta['chats']:
            return self.meta['chats'][identifier]
        if not previous:
            self.meta['starts'].append({'operation': operation, 'demo': agent['provider'] == 'demo', 'intent': intent})
        self.save()
        self.started.add(operation)
        if agent['provider'] == 'demo':
            launched = await self.client.launch(PreparedLaunch(backend_id='claude', adapter_id='claude-sdk',
                protocol='claude-agent-sdk', argv=[], cwd=str(self.work), transport={'kind': 'sdk'}),
                launcher=self.launcher, operation_id=operation)
            if launched['status'] != 'completed':
                raise ConversationError('startup', 'Demo worker did not start', uncertain=launched['status']=='uncertain', recovery=launched.get('recovery'))
            connection = await self.client.connect(launched['result']['connection_ref'])
            try:
                conversation = await connection.create_conversation(operation_id='create:' + identifier)
            except BaseException:
                await connection.disconnect()
                raise
        else:
            settings = {'model': body['model']} if body.get('model', '').strip() else {}
            conversation = await self.client.start_conversation(agent=agent['reference'],
                cwd=body.get('cwd') or str(self.work), settings=settings, operation_id=operation)
        self.handles[identifier] = conversation
        entry = dict(id=identifier, title=body.get('title') or 'New conversation', provider=agent['provider'],
            agent=agent['label'], reference=conversation.reference, startup=operation,
            created=datetime.now(timezone.utc).isoformat(), messages=[])
        self.meta['chats'][identifier] = entry
        self.save()
        return entry

    async def route(self, method, path, query, body):
        identifier = query.get('id', [body.get('id')])[0]
        if method == 'GET' and path == '/api/agents':
            return await self.agents_list()
        if method == 'GET' and path == '/api/chats':
            return {'chats': [{k: v for k, v in item.items() if k != 'reference'} for item in self.meta['chats'].values()]}
        if method == 'POST' and path == '/api/start':
            return await self.start(body)
        if method == 'GET' and path == '/api/recovery':
            entry = self.meta['chats'][identifier]
            value = {'startup': await self.client.operation(entry['startup']),
                     'queue': await self.client.recorded_queue(entry['reference'])}
            queue_id = query.get('queue_id', [None])[0]
            if queue_id:
                value['delivery'] = await self.client.inspect_queued(entry['reference'], queue_id)
            return value
        if method == 'POST' and path == '/api/reconnect':
            self.detached.discard(identifier)
            old = self.handles.pop(identifier, None)
            if old:
                await old.disconnect()
            await self.get(identifier)
            return {'connected': True}
        if method == 'POST' and path == '/api/detach':
            conversation = await self.get(identifier)
            await conversation.disconnect()
            self.handles.pop(identifier, None)
            self.detached.add(identifier)
            return {'connected': False}
        if method == 'POST' and path == '/api/stop':
            entry = self.meta['chats'][identifier]
            if entry['provider'] == 'demo':
                connection = entry['reference']['recovery']['connection_ref']
                result = await self.launcher.terminate(connection['runtime_ref'], operation_id='stop:' + uuid.uuid4().hex)
            else:
                result = await self.client.stop_startup(entry['startup'], operation_id='stop:' + uuid.uuid4().hex)
            if result['status'] == 'completed':
                selected = entry['reference']['connection_id']
                for chat_id, chat in self.meta['chats'].items():
                    if chat['reference']['connection_id'] != selected:
                        continue
                    chat['backend_stopped'] = True
                    self.detached.add(chat_id)
                    handle = self.handles.pop(chat_id, None)
                    if handle:
                        await handle.disconnect()
                self.save()
            return result
        if method == 'GET' and path == '/api/history':
            reference = self.meta['chats'][identifier]['reference']
            events, cursor = [], 0
            while True:
                page = await self.client.history(reference, cursor=cursor, limit=500)
                events.extend(page['entries'])
                if not page['has_more']:
                    break
                cursor = page['cursor']
            results = {}
            for event in events:
                if event['kind'] == 'turn.completed' and event.get('turn_id'):
                    results[event['turn_id']] = await self.client.turn_result(reference, event['turn_id'])
            return {'events': events, 'results': results, 'chat': self.meta['chats'][identifier],
                    'queue': await self.client.recorded_queue(reference)}
        if method == 'GET' and path == '/api/sync':
            if identifier in self.detached or self.meta['chats'][identifier].get('backend_stopped'):
                return {'connected': False, 'events': [], 'chat': self.meta['chats'][identifier],
                        'availability': 'stopped' if self.meta['chats'][identifier].get('backend_stopped') else 'detached'}
            conversation = await self.get(identifier)
            page = await conversation.connection.event_page(conversation.reference,
                after_sequence=int(query.get('after', ['0'])[0]))
            state = await conversation.state()
            results = {}
            for event in page['events']:
                if event['kind'] == 'turn.completed' and event.get('turn_id'):
                    results[event['turn_id']] = await conversation.turn_result(event['turn_id'])
            return dict(connected=True, events=page['events'], cursor=page['cursor'], state=state,
                results=results, chat=self.meta['chats'][identifier])
        conversation = await self.get(identifier)
        if method == 'POST' and path == '/api/queue':
            return await conversation.connection.update_queue(conversation.reference, body['action'],
                operation_id='ui:' + body['operation_id'],
                **{key: body[key] for key in ('text', 'mode', 'expected_turn_id', 'queue_id') if key in body})
        if method == 'GET' and path == '/api/commands':
            return await conversation.list_slash_commands()
        if method == 'GET' and path == '/api/settings':
            return await conversation.configuration()
        operation = 'ui:' + str(body.get('operation_id') or uuid.uuid4().hex)
        if method == 'POST' and path in ('/api/send', '/api/command'):
            if path == '/api/send':
                result = await conversation.send(body['text'], operation_id=operation)
                text = body['text']
            else:
                result = await conversation.command(body['name'], body.get('arguments', ''), operation_id=operation)
                text = '/' + body['name'] + (' ' + body['arguments'] if body.get('arguments') else '')
            if result['status'] not in ('accepted', 'completed'):
                raise ConversationError((result.get('error') or {}).get('code', 'operation'),
                    (result.get('error') or {}).get('message', 'Operation did not complete'),
                    uncertain=result['status']=='uncertain', recovery={'receipt': result})
            entry = self.meta['chats'][identifier]
            if not any(item['operation_id'] == operation for item in entry['messages']):
                entry['messages'].append(dict(text=text, turn_id=(result.get('result') or {}).get('turn_id'), operation_id=operation))
            if entry['title'] == 'New conversation':
                entry['title'] = text[:45]
            self.save()
            return result
        if method == 'POST' and path == '/api/cancel':
            state = await conversation.state()
            if not state['active_turn']:
                raise ConversationError('conflict', 'No active turn to cancel')
            return await conversation.cancel(state['active_turn']['turn_id'], operation_id='cancel:' + state['active_turn']['turn_id'])
        if method == 'POST' and path == '/api/respond':
            return await conversation.respond(body['request_id'], body['response'], operation_id='answer:' + body['request_id'])
        if method == 'POST' and path == '/api/configure':
            return await conversation.set_config(body['name'], body['value'], operation_id=operation)
        raise ConversationError('not_found', 'Unknown route')

    async def shutdown(self):
        await asyncio.gather(*(handle.disconnect() for handle in self.handles.values()), return_exceptions=True)
        if not self.args.stop_workers_on_exit:
            return
        for start in self.meta['starts']:
            if start['operation'] not in self.started:
                continue
            try:
                if start['demo']:
                    row = await self.client.operation(start['operation'])
                    ref = (row.get('result') or {}).get('connection_ref') or row.get('recovery', {}).get('connection_ref')
                    if ref:
                        await self.launcher.terminate(ref['runtime_ref'], operation_id='exit:' + uuid.uuid4().hex)
                else:
                    await self.client.stop_startup(start['operation'], operation_id='exit:' + uuid.uuid4().hex)
            except Exception:
                pass


async def main(args):
    app = App(args)
    async def handle(reader, writer):
        status, mime, data = 200, 'application/json', b''
        try:
            header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 10)
            lines = header.decode('latin1').split('\r\n')
            method, target, _ = lines[0].split(' ')
            headers = dict(line.split(':', 1) for line in lines[1:] if ':' in line)
            headers = {key.lower(): value.strip() for key, value in headers.items()}
            allowed = {f'{host}:{args.port}' for host in ['127.0.0.1', 'localhost', args.host, *args.allowed_host]}
            if headers.get('host') not in allowed:
                raise PermissionError('Invalid local host')
            if headers.get('origin') and headers['origin'] not in {'http://' + host for host in allowed}:
                raise PermissionError('Cross-origin requests are not allowed')
            length = int(headers.get('content-length', '0'))
            if not 0 <= length <= 1024 * 1024:
                raise ValueError('Request exceeds 1 MiB')
            if method == 'POST' and headers.get('content-type', '').split(';')[0] != 'application/json':
                raise PermissionError('JSON requests are required')
            body = json.loads(await reader.readexactly(length)) if length else {}
            url = urlsplit(target)
            if method == 'GET' and url.path in ('/', '/app.js', '/style.css'):
                filename = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}[url.path]
                mime = {'index.html': 'text/html', 'app.js': 'text/javascript', 'style.css': 'text/css'}[filename]
                data = (ROOT / 'static' / filename).read_bytes()
            else:
                result = await app.route(method, url.path, parse_qs(url.query), body)
                data = json.dumps(result).encode()
        except PermissionError as error:
            status, data = 403, json.dumps({'error': {'message': str(error)}}).encode()
        except ConversationError as error:
            status, data = 409, json.dumps({'error': error.to_dict()}).encode()
        except Exception as error:
            status, data = 400, json.dumps({'error': {'message': str(error)}}).encode()
        try:
            writer.write(f'HTTP/1.1 {status} Response\r\nContent-Type: {mime}; charset=utf-8\r\nContent-Length: {len(data)}\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nConnection: close\r\n\r\n'.encode() + data)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
    server = await asyncio.start_server(handle, args.host, args.port, limit=65536)
    args.port = server.sockets[0].getsockname()[1]
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, stopped.set)
    print(f'Listening: http://{args.host}:{args.port} · default mode: offline demo', flush=True)
    try:
        async with server:
            await stopped.wait()
    finally:
        await app.shutdown()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--allowed-host', action='append', default=[], help='Additional HTTP hostname or IP address (without port)')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--stop-workers-on-exit', action='store_true', help='Stop workers started by this web bridge run on exit; default: detach and retain')
    parser.add_argument('--state', default=str(ROOT / '.state'))
    parser.add_argument('--live', action='store_true', help='Expose configured local Codex/OpenCode environments; starting a live chat uses account quota')
    parser.add_argument('--agent-config')
    parser.add_argument('--agent-root')
    asyncio.run(main(parser.parse_args()))
