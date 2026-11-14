"""Measure real Codex processes with disposable Conversation Manager workers.

Run with the same Python environment as Conversation Manager. Uses an existing
authenticated Agent Manager environment, read-only threads, and tiny inference
requests. Only processes launched by this run are measured or stopped.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import statistics
import tempfile
import time

from conversation_manager import AgentEnvironments, Client
from conversation_manager.shared_codex import pool_directory, owner_state, live


def memory(roots):
    parents = {}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status = dict(line.split(':', 1) for line in (entry / 'status').read_text().splitlines() if ':' in line)
            parents[int(entry.name)] = int(status['PPid'])
        except (OSError, ValueError, KeyError):
            pass
    owned = set(roots)
    while True:
        found = owned | {p for p, parent in parents.items() if parent in owned}
        if owned == found:
            break
        owned = found
    total = dict(pss_mib=0, rss_mib=0, private_mib=0, swap_mib=0)
    count = 0
    for pid in owned:
        try:
            rows = dict(line.split(':', 1) for line in Path(f'/proc/{pid}/smaps_rollup').read_text().splitlines() if ':' in line)
            for field, keys in [('pss_mib', ['Pss']), ('rss_mib', ['Rss']),
                                ('private_mib', ['Private_Clean', 'Private_Dirty']), ('swap_mib', ['Swap'])]:
                total[field] += sum(int(rows.get(key, '0').split()[0]) for key in keys) / 1024
            count += 1
        except (OSError, ValueError):
            pass
    return dict(processes=count, **{k: round(v, 2) for k,v in total.items()})


async def trial(agents, environment, count, mode, number, inference):
    with tempfile.TemporaryDirectory(prefix='codex-ram-benchmark-') as temporary:
        root = Path(temporary)
        client = Client(root / 'state')
        descriptor = agents.prepare_native(environment)
        folder = pool_directory(client.home, descriptor, root) if mode == 'shared' else None
        refs, chats = [], []
        result = dict(mode=mode, conversations=count, repetition=number)
        try:
            started = time.monotonic()
            for i in range(count):
                launch = await client.launch_codex(environment, cwd=root, agents=agents,
                    settings={'model': 'gpt-6.1-sol', 'access': {'sandbox': 'read-only', 'approvalPolicy': 'on-request'}},
                    server_mode=mode, operation_id=f'benchmark-launch-{i}')
                ref = (launch.get('result') or {}).get('connection_ref') or launch.get('recovery', {}).get('connection_ref')
                if ref:
                    refs.append(ref)
                if launch['status'] != 'completed':
                    raise RuntimeError(str(launch.get('error')))
                connection = await client.connect(ref)
                chat = await connection.create_conversation(operation_id=f'benchmark-create-{i}')
                chats.append(chat)
            result['startup_seconds'] = round(time.monotonic() - started, 3)
            roots = {ref['runtime_ref']['pid'] for ref in refs}
            if folder:
                state = owner_state(folder)
                if live(state):
                    roots.add(state['pid'])
            await asyncio.sleep(1)
            result['idle_before'] = memory(roots)
            if inference:
                async def turn(i, chat):
                    sent = await chat.send(f'Reply exactly RAMBENCH-{i}. Do not call tools.', operation_id=f'benchmark-send-{i}')
                    async with asyncio.timeout(180):
                        async for event in chat.events():
                            if event['kind'] == 'turn.completed':
                                break
                    output = await chat.turn_result(sent['result']['turn_id'])
                    if output['status'] != 'completed':
                        raise RuntimeError(str(output))
                    return output['final_text']
                started = time.monotonic()
                task = asyncio.gather(*(turn(i,c) for i,c in enumerate(chats)))
                peak = result['idle_before']
                samples = 0
                try:
                    while not task.done():
                        sample = memory(roots)
                        if sample['pss_mib'] > peak['pss_mib']:
                            peak = sample
                        samples += 1
                        await asyncio.sleep(.25)
                    result['replies'] = await task
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                result['concurrent_turn_seconds'] = round(time.monotonic() - started, 3)
                result['active_peak'] = peak
                result['samples'] = samples
                await asyncio.sleep(1)
                result['idle_after'] = memory(roots)
            return result
        finally:
            for chat in chats:
                await chat.disconnect()
            for i, ref in enumerate(refs):
                await client.terminate_runtime(ref['runtime_ref'], operation_id=f'benchmark-stop-{i}')
            if folder:
                state = owner_state(folder)
                if live(state):
                    os.kill(state['pid'], signal.SIGTERM)
                    async with asyncio.timeout(15):
                        while live(owner_state(folder)):
                            await asyncio.sleep(.05)


async def main(args):
    agents = AgentEnvironments('codex')
    environment = args.environment or agents.list()[0]['environment_id']
    results = []
    for number in range(args.repetitions):
        modes = ['separate', 'shared'] if number % 2 == 0 else ['shared', 'separate']
        for mode in modes:
            result = await trial(agents, environment, args.count, mode, number+1, not args.idle_only)
            results.append(result)
            print(json.dumps(result), flush=True)
            Path(args.output).write_text(json.dumps(dict(results=results), indent=2) + '\n')
    summary = {}
    for stage in ['idle_before', 'active_peak', 'idle_after']:
        means = {mode: statistics.mean(r[stage]['pss_mib'] for r in results if r['mode'] == mode and stage in r)
                 for mode in ['separate', 'shared'] if any(stage in r for r in results)}
        if means:
            summary[stage] = dict(**means, savings_percent=round(100*(means['separate']-means['shared'])/means['separate'], 1))
    Path(args.output).write_text(json.dumps(dict(results=results, summary=summary), indent=2) + '\n')
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment')
    parser.add_argument('--count', type=int, default=5, choices=range(1, 11))
    parser.add_argument('--repetitions', type=int, default=3, choices=range(1, 6))
    parser.add_argument('--idle-only', action='store_true')
    parser.add_argument('--output', required=True)
    asyncio.run(main(parser.parse_args()))
