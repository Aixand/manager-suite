"""Standalone package qualification; public imports only, no real provider calls."""
import argparse
import asyncio
from contextlib import aclosing
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import undercurrent
from agent_manager import Client, Manager, SDK_CONTRACT_VERSION

BASE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--uc-bin', type=Path, required=True)
parser.add_argument('--recover', action='store_true')
OPTIONS = parser.parse_args()
BIN = OPTIONS.uc_bin.resolve()
ROOT = BASE / 'registry'
CONFIG = BASE / 'providers.toml'
WORK = BASE / 'project'
HOME = BASE / 'runtime'
checks = []

def passed(name, **details):
    row = dict(check=name, **details)
    checks.append(row)
    print(json.dumps(row), flush=True)

def runtime(timeout=15):
    return undercurrent.Client(home=HOME, bridge=str(BIN / 'uc-python-bridge'), request_timeout=timeout)

def client(rt):
    return Client('codex', config=CONFIG, root=ROOT, runtime=rt)

async def terminal_ready(agent, after_revision=None):
    return await agent.wait(lifecycle='running', interaction='available',
                            after_revision=after_revision, timeout=20)

async def stop(agent):
    await agent.stop(force=True)
    await agent.wait(lifecycle=('stopped', 'exited', 'failed'), timeout=15)

async def run_example(command, *arguments):
    child = await asyncio.create_subprocess_exec(sys.executable, '-I', str(BASE / 'example.py'),
        command, '--provider', 'codex', '--config', str(CONFIG), '--root', str(ROOT),
        '--runtime-home', str(HOME), '--bridge', str(BIN / 'uc-python-bridge'),
        '--cwd', str(WORK), *arguments,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        output, error = await asyncio.wait_for(child.communicate(), timeout=90)
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
    assert child.returncode == 0, error.decode()
    return output

async def consume():
    import agent_manager
    from importlib.metadata import distributions
    assert SDK_CONTRACT_VERSION == 1
    assert 'site-packages' in Path(agent_manager.__file__).parts
    assert 'site-packages' in Path(undercurrent.__file__).parts
    packages = sorted(item.metadata['Name'] for item in distributions())
    installed = {name.lower().replace('_', '-') for name in packages}
    assert installed.isdisjoint({'server-manager', 'carrier', 'runner', 'agent-runner',
                                 'manager-suites', 'mcp-manager',
                                 'skill-manager', 'prompt-manager'})
    passed('package-isolation', packages=packages)
    async with runtime() as rt:
        async with client(rt) as agents:
            assert len(await agents.profiles()) == 2
            assert len(await agents.environments()) == 2
            budgets = await agents.budgets(refresh=True)
            assert sorted(row['buckets'][0]['remaining_percent'] for row in budgets) == [30, 85]
            saved = await agents.sessions(cwd=WORK)
            assert len(saved) == 1
            assert await agents.sessions(cwd=BASE / 'other') == []
            passed('inventory-budget-folder-sessions', budgets=[30, 85])
            output = await run_example('inventory')
            inventory = json.loads(output)
            assert len(inventory['profiles']) == len(inventory['environments']) == 2
            assert len(inventory['sessions']) == 1
            passed('maintained-example-inventory')

            key = str(uuid.uuid4())
            automatic_reference = BASE / 'automatic-reference.json'
            await run_example('launch', '--idempotency-key', key, '--reference', str(automatic_reference))
            selected = await agents.get(json.loads(automatic_reference.read_text()))
            assert selected.profile == 'high'
            assert selected.selection.remaining_percent == 85
            assert (await agents.start(cwd=WORK, idempotency_key=key)).run_id == selected.run_id
            await terminal_ready(selected)
            state = await selected.inspect()
            assert state.lifecycle == 'running'
            async with aclosing(selected.watch()) as stream:
                update = await asyncio.wait_for(anext(stream), timeout=10)
                assert update.state.lifecycle == 'running'
            passed('automatic-ranking-idempotency-state-watch', profile=selected.profile)

            receipt = await selected.send_input('hello fixture', delivery_id='consumer-message-1')
            await terminal_ready(selected, receipt.foreground_revision)
            assert b'RESULT:hello fixture' in await selected.read_screen()
            duplicate = await selected.send_input('hello fixture', delivery_id='consumer-message-1')
            assert duplicate.duplicate
            page = await selected.read_output(from_offset=0, max_bytes=65536)
            assert b'RESULT:hello fixture' in page.data
            passed('input-output-delivery-deduplication')
            reference = selected.reference.to_dict()
            (BASE / 'reference.json').write_text(json.dumps(reference, indent=2))
            await agents.close()
            assert (await rt.inspect(selected.session_id)).lifecycle == 'running'
            passed('client-detach-preserves-running-agent')

    child = await asyncio.create_subprocess_exec(sys.executable, '-I', str(Path(__file__).resolve()), '--recover', '--uc-bin', str(BIN),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    output, error = await asyncio.wait_for(child.communicate(), timeout=20)
    assert child.returncode == 0, error.decode()
    assert json.loads(output)['session_id'] == selected.session_id
    passed('separate-process-reference-recovery')

    async with runtime(timeout=25) as rt:
        async with client(rt) as agents:
            recovered = await agents.get(json.loads((BASE / 'reference.json').read_text()))
            assert recovered.session_id == selected.session_id
            retry = await agents.start(cwd=WORK, idempotency_key=key)
            assert retry.run_id == selected.run_id
            receipt = await recovered.send_input('after reconnect', delivery_id='consumer-message-2')
            await terminal_ready(recovered, receipt.foreground_revision)
            assert b'RESULT:after reconnect' in await recovered.read_screen()
            passed('new-connection-reference-and-key-recovery')
            await stop(recovered)

            explicit_key = str(uuid.uuid4())
            example_reference = BASE / 'example-reference.json'
            await run_example('launch', '--profile', 'low', '--idempotency-key', explicit_key,
                              '--reference', str(example_reference))
            explicit = await agents.get(json.loads(example_reference.read_text()))
            await terminal_ready(explicit)
            assert explicit.profile == 'low'
            assert (await agents.spawn(profile='low', cwd=WORK, idempotency_key=explicit_key)).run_id == explicit.run_id
            await run_example('recover', '--reference', str(example_reference),
                              '--message', 'from example', '--delivery-id', 'example-message-1')
            async with asyncio.timeout(20):
                while b'RESULT:from example' not in await explicit.read_screen():
                    await asyncio.sleep(.05)
            await run_example('recover', '--reference', str(example_reference), '--stop')
            assert (await explicit.inspect()).lifecycle in ('stopped', 'exited', 'failed')
            passed('maintained-example-explicit-launch-input-recover-stop')

            await run_example('continue', '--session', saved[0]['ref'], '--profile', 'high',
                              '--reference', str(example_reference))
            resumed = await agents.get(json.loads(example_reference.read_text()))
            await terminal_ready(resumed)
            assert resumed.thread_id == NATIVE_ID
            observed = json.loads((Path(resumed.home) / 'launch.json').read_text())
            assert NATIVE_ID in observed['args'] and 'resume' in observed['args']
            assert observed['cwd'] == str(WORK)
            await stop(resumed)
            passed('maintained-example-saved-native-session-continuation', thread_id=resumed.thread_id)
            rows = await agents.runs()
            assert len(rows) == 3, len(rows)
            passed('no-duplicate-managed-executions', count=len(rows))

async def main():
    os.environ['HOME'] = str(BASE / 'user')
    os.environ['USERPROFILE'] = str(BASE / 'user')
    for key, directory in (('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'), ('XDG_CACHE_HOME', 'cache')):
        os.environ[key] = str(BASE / 'user' / directory)
    for path in (BASE / 'user', WORK, BASE / 'other', HOME):
        path.mkdir(mode=0o700, exist_ok=True)
    CONFIG.write_text('[providers.codex]\nexecutable=' + json.dumps(str(BASE / 'codex')) + '\n')
    manager = Manager('codex', config=CONFIG, root=ROOT)
    for name, remaining in [('low', 30), ('high', 85)]:
        manager.new_profile(name)
        home = manager.home(name)
        (home / 'quota-fixture.json').write_text(json.dumps({'rateLimitsByLimitId': {'codex': {
            'primary': {'usedPercent': 100 - remaining, 'resetsAt': time.time() + 86400, 'windowDurationMins': 300}}}}))
    manager.set_default('low')
    home = manager.home('high')
    (home / 'sessions').mkdir()
    (home / 'sessions' / 'fixture.jsonl').write_text(json.dumps({'type': 'session_meta', 'payload': {
        'id': NATIVE_ID, 'cwd': str(WORK)}}) + '\n')
    with (BASE / 'daemon.log').open('w') as log:
        daemon = subprocess.Popen([str(BIN / 'uc-daemon')], env=os.environ | {'UNDERCURRENT_HOME': str(HOME)},
                                  stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        try:
            async with asyncio.timeout(15):
                while not (HOME / 'run/control.sock').exists():
                    assert daemon.poll() is None, 'isolated daemon exited'
                    await asyncio.sleep(.05)
            await consume()
        finally:
            try:
                if daemon.poll() is None and (HOME / 'run/control.sock').exists():
                    async with asyncio.timeout(15):
                        async with runtime() as rt:
                            for session in await rt.list():
                                if session.lifecycle not in ('stopped', 'exited', 'failed'):
                                    await rt.stop(session.id, force=True)
            finally:
                if daemon.poll() is None:
                    daemon.terminate()
                    try:
                        await asyncio.to_thread(daemon.wait, timeout=10)
                    except subprocess.TimeoutExpired:
                        daemon.kill()
                        await asyncio.to_thread(daemon.wait)
    (BASE / 'results.json').write_text(json.dumps(dict(contract=SDK_CONTRACT_VERSION, checks=checks), indent=2))

NATIVE_ID = str(uuid.uuid4())
async def recover():
    async with runtime() as rt:
        async with client(rt) as agents:
            agent = await agents.get(json.loads((BASE / 'reference.json').read_text()))
            assert (await agent.inspect()).lifecycle == 'running'
            print(json.dumps({'session_id': agent.session_id}))

if __name__ == '__main__':
    asyncio.run(recover() if OPTIONS.recover else main())
