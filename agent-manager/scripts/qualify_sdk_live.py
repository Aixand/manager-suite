"""Opt-in live Codex SDK qualification using an existing profile and daemon."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import uuid

from agent_manager import Client, RunNotFoundError, SDK_CONTRACT_VERSION

TERMINAL = ('stopped', 'exited', 'failed')


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, help='Existing Codex profile to use')
    parser.add_argument('--continue-profile', help='Resume in this other existing Codex profile (default: source profile)')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--runtime-home', type=Path)
    parser.add_argument('--bridge')
    parser.add_argument('--model', help='Optional native model override')
    parser.add_argument('--reasoning-effort', choices=('low', 'medium', 'high'),
                        help='Optional per-launch Codex reasoning effort; does not change the profile')
    parser.add_argument('--timeout', type=float, default=120, help='Seconds per readiness/response check')
    parser.add_argument('--output', type=Path, help='New directory for the disposable project and report')
    parser.add_argument('--allow-live', action='store_true', help='Explicitly permit three small model turns')
    parser.add_argument('--preflight-only', action='store_true', help='Inspect setup and allowance without launching an agent')
    parser.add_argument('--recover', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0 or args.timeout > 600:
        parser.error('--timeout must be greater than zero and at most 600 seconds')
    if not args.allow_live and not args.preflight_only and args.recover is None:
        parser.error('Use --allow-live to permit model calls, or --preflight-only')
    return args


def connection(args):
    import undercurrent
    options = {}
    if args.runtime_home is not None:
        options['home'] = args.runtime_home
    if args.bridge is not None:
        options['bridge'] = args.bridge
    return undercurrent.Client(**options)


def client(args, runtime):
    return Client('codex', config=args.config, root=args.root, runtime=runtime)


def child_arguments(args, reference):
    command = [sys.executable, '-I', str(Path(__file__).resolve()), '--profile', args.profile,
               '--recover', str(reference)]
    for name in ('config', 'root', 'runtime_home', 'bridge'):
        value = getattr(args, name)
        if value is not None:
            command.extend(['--' + name.replace('_', '-'), str(value)])
    return command


async def recover(args):
    async with connection(args) as runtime:
        async with client(args, runtime) as agents:
            agent = await agents.get(json.loads(args.recover.read_text()))
            state = await agent.inspect()
            if state.lifecycle != 'running':
                raise RuntimeError('Recovered execution is not running')
            print(json.dumps({'run_id': agent.run_id, 'session_id': agent.session_id}))


async def ready(agent, runtime, timeout):
    # Trust only the disposable qualification folder, never arbitrary provider dialogs.
    screen = (await agent.read_screen()).decode('utf-8', 'replace')
    if 'Do you trust' in screen and ('Yes, I trust' in screen or 'Yes, proceed' in screen):
        await agent.send_key('enter')
    return await runtime.wait_for_stability(agent.session_id, lifecycle='running',
        interaction='available', stable_for=2, timeout=timeout)


async def exchange(agent, runtime, prompt, expected, timeout, delivery_id):
    if expected in prompt:
        raise ValueError('Response marker must not appear in the submitted prompt')
    await ready(agent, runtime, timeout)
    receipt = await agent.send_input(prompt, delivery_id=delivery_id)
    async with asyncio.timeout(timeout):
        while True:
            state = await agent.inspect()
            if state.lifecycle in TERMINAL:
                raise RuntimeError('Execution terminated before its expected response')
            screen = (await agent.read_screen()).decode('utf-8', 'replace')
            if re.search(r'(?<!\d)' + re.escape(expected) + r'(?!\d)', screen):
                await runtime.wait_for_stability(agent.session_id, lifecycle='running',
                    interaction='available', stable_for=2, after_revision=receipt.foreground_revision,
                    timeout=timeout)
                return
            await asyncio.sleep(.25)


async def stop(agent, timeout):
    if (await agent.inspect()).lifecycle not in TERMINAL:
        await agent.stop()
    await agent.wait(lifecycle=TERMINAL, timeout=timeout)


async def cleanup(args, owned, keys):
    """Recover only this invocation's identities; never enumerate or stop other runs."""
    if not owned and not keys:
        return []
    errors = []
    try:
        async with connection(args) as runtime:
            async with client(args, runtime) as agents:
                stopped = set()
                for ref in [*owned, *keys]:
                    try:
                        agent = await agents.get(ref)
                        if agent.session_id not in stopped:
                            await stop(agent, min(args.timeout, 30))
                            stopped.add(agent.session_id)
                    except RunNotFoundError:
                        # A missing unacknowledged key has no registry execution to recover.
                        # A missing acknowledged reference cannot establish process absence.
                        if isinstance(ref, dict):
                            errors.append('RunNotFoundError')
                    except Exception as error:
                        errors.append(type(error).__name__)
    except Exception as error:
        errors.append(type(error).__name__)
    return errors


async def qualify(args):
    output = args.output.expanduser().resolve() if args.output else Path(tempfile.mkdtemp(prefix='agent-sdk-live-'))
    if args.output:
        output.mkdir(parents=True, mode=0o700)  # Never overwrite a previous report/project.
    os.chmod(output, 0o700)
    project = output / 'project'
    project.mkdir()
    (project / 'AGENTS.md').write_text('This is a disposable SDK qualification project.\n'
        'Answer the requested small text checks directly. Do not use tools, access files, '
        'spawn agents, or make changes.\n')
    report = dict(schema='agent-manager/live-qualification-1', contract=SDK_CONTRACT_VERSION,
                  provider='codex', profile=args.profile, continue_profile=args.continue_profile or args.profile,
                  reasoning_effort=args.reasoning_effort, started_at=datetime.now(timezone.utc).isoformat(),
                  checks=[], executions=[], status='running')
    stage = 'preflight'
    owned = []
    keys = []
    failure = None

    def record(check, **details):
        report['checks'].append(dict(check=check, **details))
        save()
        print('PASS:', check, flush=True)

    def save():
        path = output / 'report.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2) + '\n')
        temporary.replace(path)

    def retained(agent):
        owned.append(agent.reference.to_dict())
        report['executions'].append(dict(run_id=agent.run_id, session_id=agent.session_id))
        save()

    print('Live qualification artifacts:', output, flush=True)
    save()
    try:
        async with connection(args) as runtime:
            async with client(args, runtime) as agents:
                readiness = await agents.readiness(args.profile, refresh=True)
                report['installation'] = dict(version=readiness['installation']['version'])
                report['login'] = readiness['login']['state']
                report['features'] = {key: dict(supported=value['supported'],
                    installed_supported=value['installed_supported']) for key, value in readiness['features'].items()}
                report['continuation'] = (await agents.get_profile(args.profile))['capabilities'].get('crossEnvironmentResume')
                if not readiness['ready'] or report['login'] != 'signed-in':
                    raise RuntimeError('Profile is not ready and signed in')
                budget = await agents.budget(args.profile, refresh=True, cwd=project, model=args.model)
                report['budget'] = dict(status=budget['status'], scope=budget['scope'],
                    buckets=[dict(id=b['id'], remaining_percent=b.get('remaining_percent')) for b in budget['buckets']])
                if budget['status'] not in ('ok', 'partial'):
                    raise RuntimeError('Fresh usable allowance is unavailable')
                selected = await agents.select_profile(model=args.model)
                profiles = await agents.profiles()
                report['selection'] = dict(profile=selected['profile'], **selected['selection'])
                report['ranking_verification'] = ('single-environment-only' if len({p['environment_id'] for p in profiles}) == 1
                                                  else 'selection-observed-only')
                destination = await agents.get_profile(args.continue_profile or args.profile)
                if args.continue_profile:
                    origin = await agents.get_profile(args.profile)
                    if origin['environment_id'] == destination['environment_id']:
                        raise ValueError('Cross-profile qualification requires distinct environments, not aliases')
                    target_readiness = await agents.readiness(args.continue_profile, refresh=True)
                    if not target_readiness['ready'] or target_readiness['login']['state'] != 'signed-in':
                        raise RuntimeError('Destination profile is not ready and signed in')
                    report['destination'] = dict(profile=destination['profile'], environment_id=destination['environment_id'])
                record('preflight-version-login-budget-selection')
                if args.preflight_only:
                    report['status'] = 'preflight-passed'
                    return output

                stage = 'spawn'
                key = str(uuid.uuid4())
                keys.append(key)
                report['idempotency_keys'] = keys
                save()  # Retain intent before dispatch, including uncertain startup outcomes.
                launch = dict(profile=args.profile, cwd=project, idempotency_key=key, timeout=args.timeout)
                if args.model:
                    launch['model'] = args.model
                if args.reasoning_effort:
                    launch['native_config'] = {'model_reasoning_effort': args.reasoning_effort}
                agent = await agents.spawn(**launch)
                retained(agent)
                await ready(agent, runtime, args.timeout)
                retry = await agents.spawn(**launch)
                if retry.reference != agent.reference:
                    raise RuntimeError('Idempotent launch returned a different execution')
                record('spawn-readiness-idempotency', run_id=agent.run_id)

                stage = 'first-response'
                digits = [secrets.choice('123456789') for _ in range(10)]
                secret = ''.join(digits)
                a, b = secrets.randbelow(90000000) + 10000000, secrets.randbelow(90000000) + 10000000
                prompt = ('Remember these digits in order for a later question: ' + ', '.join(digits) + '. '
                    f'For this reply, output only the decimal result of {a} + {b}. Do not use tools.')
                await exchange(agent, runtime, prompt, str(a + b), args.timeout, 'live-first-' + key)
                record('first-model-response')
                reference = output / 'reference.json'
                reference.write_text(json.dumps(agent.reference.to_dict(), indent=2))

        stage = 'separate-process-recovery'
        child = await asyncio.create_subprocess_exec(*child_arguments(args, reference),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, _ = await asyncio.wait_for(child.communicate(), timeout=30)
        finally:
            if child.returncode is None:
                child.kill()
                await child.wait()
        if child.returncode or json.loads(stdout)['session_id'] != agent.session_id:
            raise RuntimeError('Separate process could not recover the execution')
        record('detach-and-separate-process-recovery')

        async with connection(args) as runtime:
            async with client(args, runtime) as agents:
                stage = 'reconnected-response'
                agent = await agents.get(json.loads(reference.read_text()))
                a, b = secrets.randbelow(90000000) + 10000000, secrets.randbelow(90000000) + 10000000
                await exchange(agent, runtime, f'Output only the decimal result of {a} + {b}. Do not use tools.',
                               str(a + b), args.timeout, 'live-second-' + key)
                record('reconnected-model-response')
                stage = 'stop-and-discover'
                await stop(agent, args.timeout)
                async with asyncio.timeout(20):
                    while True:
                        saved = await agents.sessions(cwd=project)
                        if len(saved) == 1:
                            break
                        await asyncio.sleep(.25)
                record('stop-and-folder-session-discovery', native_session_id=saved[0]['native_id'])
                stage = 'native-continuation'
                continuation_key = str(uuid.uuid4())
                keys.append(continuation_key)
                save()
                options = dict(profile=args.continue_profile or args.profile, idempotency_key=continuation_key, timeout=args.timeout)
                if args.model:
                    options['model'] = args.model
                if args.reasoning_effort:
                    options['native_config'] = {'model_reasoning_effort': args.reasoning_effort}
                resumed = await agents.continue_session(saved[0]['ref'], **options)
                retained(resumed)
                if resumed.environment_id != destination['environment_id'] or Path(resumed.home).resolve() != Path(destination['home']).resolve():
                    raise RuntimeError('Continuation did not select the destination environment and home')
                await exchange(resumed, runtime,
                    'Reply only with the digits I asked you to remember earlier, concatenated without separators. Do not use tools.',
                    secret, args.timeout, 'live-continued-' + continuation_key)
                record(('cross-profile' if args.continue_profile else 'same-profile') + '-native-continuation-retains-memory',
                       environment_id=resumed.environment_id)
                await stop(resumed, args.timeout)
                record('explicit-shutdown')
        report['status'] = 'passed'
    except BaseException as error:
        failure = error
        report.update(status='failed', failure=dict(stage=stage, exception=type(error).__name__))
        print('FAIL:', stage, type(error).__name__, flush=True)
    finally:
        cleanup_errors = await cleanup(args, owned, keys)
        report['cleanup'] = dict(status='failed' if cleanup_errors else 'passed', errors=cleanup_errors)
        if cleanup_errors:
            report['status'] = 'failed'
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        save()
    if failure is not None or report['status'] == 'failed':
        raise SystemExit(1)
    return output


if __name__ == '__main__':
    args = arguments()
    if args.recover:
        asyncio.run(recover(args))
    else:
        asyncio.run(qualify(args))
