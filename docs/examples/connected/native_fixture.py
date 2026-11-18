"""Deterministic native Codex lifecycle/transport fixture. No inference or login.

The executable reads injected TOML, invokes the actual MCP binding hook, and
uses that injected MCP server for every message. It processes JSON terminal input.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import tomllib
import uuid

from connected_runtime import save_json, until

EXPECTED = b'Net revenue: 1175.00\n'


def prepare(app, profiles):
    from agent_manager import Manager
    os.environ['AGENT_MANAGER_HOMES_ROOT'] = str(app.root / 'homes')
    os.environ['AGENT_MANAGER_HOME'] = str(app.root / 'manager')
    os.environ['AIXAND_MANAGER_ROOT'] = str(app.root / 'suite')
    config = app.root / 'providers.toml'
    executable = app.root / 'codex-fixture'
    executable.write_text('#!' + sys.executable + '\nfrom native_fixture import native_main\nnative_main()\n')
    executable.chmod(0o700)
    config.write_text('[providers.codex]\nexecutable=' + json.dumps(str(executable)) + '\n')
    manager = Manager('codex', config=config, root=app.root / 'manager')
    for profile in profiles:
        manager.new_profile(profile)
    return dict(config=config, root=app.root / 'manager', runtime=app.runtime)


def adapter(agents, app):
    from carrier.integrations.agent_manager import CarrierAgents
    # No detached monitor process: examples explicitly stop and reconcile owners.
    return CarrierAgents(agents, url=app.url, database=app.database,
                         home=app.root / 'bindings', monitor=False)


def launch_intent(app, role):
    intent = dict(profile=role, cwd=str(app.root), idempotency_key=str(uuid.uuid4()),
                  detection=False, env={'PYTHONPATH': os.pathsep.join(sys.path)})
    save_json(app.root / (role + '-launch.json'), intent)
    return intent


async def bound(integration, intent):
    return await until(lambda: integration.inspect(intent['idempotency_key']),
                       lambda row: row['status'] == 'bound')


async def native_state(app, address):
    async def read():
        path = app.root / (address + '-native.json')
        return json.loads(path.read_text()) if path.exists() else None
    return await until(read, bool)


async def trigger(agent, app, label, command):
    text = json.dumps(command, sort_keys=True)
    intent = dict(text=text, delivery_id=str(uuid.uuid4()))
    save_json(app.root / (label + '-input.json'), intent)
    receipt = await agent.send_input(**intent)
    replay = await agent.send_input(**intent)
    assert replay.duplicate
    save_json(app.root / (label + '-input-receipt.json'), dict(delivery_id=intent['delivery_id'],
                                                            duplicate=receipt.duplicate, replay_duplicate=replay.duplicate))


async def request(inbox, app, label, to, body):
    intent = dict(to=to, content=body, idempotency_key=str(uuid.uuid4()))
    save_json(app.root / (label + '-send.json'), intent)
    message = await inbox.send_json(**intent)
    replay = await inbox.send_json(**intent)
    assert replay.id == message.id
    return message


async def checked_reply(inbox, request_message, sender, task):
    page = await inbox.wait(timeout=15, reply_to=request_message.id)
    assert page.status == 'messages', 'Reply deadline expired'
    assert len(page.messages) == 1
    message = page.messages[0]
    assert message.sender == sender and message.reply_to == request_message.id
    assert message.json()['task'] == task
    await until(lambda: inbox.delivery_status(request_message.idempotency_key),
                lambda row: row['status'] == 'acknowledged')
    return message


async def native():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    settings = {}
    args = sys.argv[1:]
    for index, item in enumerate(args):
        if item == '-c':
            settings.update(tomllib.loads(args[index + 1]).get('mcp_servers', {}).get('carrier', {}))
    parameters = StdioServerParameters(command=settings['command'], args=settings['args'], env=dict(os.environ))
    thread = str(uuid.uuid4())
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as mcp:
            await mcp.initialize()

            async def call(name, **arguments):
                result = await mcp.call_tool(name, arguments)
                if result.isError:
                    raise RuntimeError(str(result.content))
                return json.loads(next(item.text for item in result.content if item.type == 'text'))

            async def bind_once():
                result = await call('bind_conversation', session_id=thread, hook_event_name='SessionStart')
                return result.get('continue') is not False
            await until(bind_once, bool)
            identity = await call('whoami')
            address = identity['address']
            save_json(Path.cwd() / (address + '-native.json'), dict(pid=os.getpid(), thread=thread,
                                                                  address=address, fixture=True))
            print('FIXTURE_READY ' + address, flush=True)
            while True:
                line = await asyncio.to_thread(sys.stdin.readline)
                if not line:
                    return
                line = line.replace('\x1b[200~', '').replace('\x1b[201~', '').strip()
                if not line:
                    continue
                command = json.loads(line)
                page = await call('wait', timeout_seconds=10)
                rows = [row for row in page['messages'] if row['id'] == command['request_id']]
                assert len(rows) == 1
                row = rows[0]
                body = json.loads(row['content'])
                assert row['sender'] == command['sender'] and body['task'] == command['task']
                # docs:start native-accept
                journal = Path.cwd() / f"native-accepted-{row['id']}.json"
                if journal.exists():
                    delivery = json.loads(journal.read_text())
                else:
                    if body['action'] == 'echo':
                        result = dict(task=body['task'], value=body['value'].upper())
                    elif body['action'] == 'produce':
                        data = EXPECTED if body['attempt'] == 2 else b'Net revenue: 0.00\n'
                        Path(body['artifact']).write_bytes(data)
                        result = dict(task=body['task'], sha256=hashlib.sha256(data).hexdigest())
                    elif body['action'] == 'review':
                        data = Path(body['artifact']).read_bytes()
                        result = dict(task=body['task'], status='accepted' if data == EXPECTED else 'rejected',
                                      sha256=hashlib.sha256(data).hexdigest())
                    else:
                        raise ValueError('Unknown fixture action')
                    delivery = dict(content=json.dumps(result, sort_keys=True), to=row['sender'],
                                    reply_to=row['id'], message_id=str(uuid.uuid4()))
                    save_json(journal, delivery)
                await call('send', **delivery)
                await call('ack', message_id=row['id'])
                # docs:end native-accept
                print('FIXTURE_PROCESSED ' + str(row['id']), flush=True)


def native_main():
    if sys.argv[1:] == ['--version']:
        print('codex-cli 0.166.0 (deterministic fixture)')
        return
    asyncio.run(native())
