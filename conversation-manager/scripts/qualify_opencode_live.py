"""Opt-in live OpenCode route check in an isolated, disposable native environment."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import tempfile

from agent_manager import Manager
from conversation_manager import ConversationClient, ConversationError


async def done(conversation, turn_id):
    counts = Counter()
    async with asyncio.timeout(180):
        async for event in conversation.events():
            if event.get('turn_id') != turn_id:
                continue
            counts[event['kind']] += 1
            # Qualification never grants a permission or answers an open question.
            for request in await conversation.pending_requests():
                if request['kind'] == 'permission':
                    await conversation.respond(request['request_id'], {'outcome': 'selected', 'option_id': 'reject'},
                        operation_id='deny:' + request['request_id'])
            if event['kind'] == 'turn.completed':
                return dict(result=await conversation.turn_result(turn_id), event_counts=dict(counts))


async def run(args):
    report = dict(checked_at=datetime.now(timezone.utc).isoformat(), model=args.model,
        scope='isolated native environment; no user account credentials', checks={})
    with tempfile.TemporaryDirectory(prefix='cm-opencode-live-') as temporary:
        root = Path(temporary)
        work = root / 'work'
        work.mkdir()
        config = root / 'providers.toml'
        executable = shutil.which('opencode')
        config.write_text('[providers.opencode]\nexecutable=' + json.dumps(executable) + '\n')
        manager = Manager('opencode', config=config, root=root / 'agents')
        manager.new_profile('qualification')
        client = ConversationClient(root / 'state', agent_config=config, agent_root=root / 'agents')
        conversation = None
        try:
            process = await asyncio.create_subprocess_exec('git', '-C', str(work), 'init', '-q', '--template=',
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            _, error = await process.communicate()
            if process.returncode:
                raise RuntimeError(error.decode())
            (work / 'example.py').write_text('def add(a, b):\n    return a + b\n')
            commands = [
                ['git', '-C', str(work), 'add', 'example.py'],
                ['git', '-C', str(work), '-c', 'core.hooksPath=/dev/null', '-c', 'user.name=Qualification', '-c',
                 'user.email=qualification@example.invalid', 'commit', '-qm', 'Initial'],
            ]
            for command in commands:
                process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE)
                _, error = await process.communicate()
                if process.returncode:
                    raise RuntimeError(error.decode())
            (work / 'example.py').write_text('def add(a, b):\n    return a - b\n')
            agent = (await client.available_agents(providers=('opencode',)))[0]['reference']
            print('Starting isolated OpenCode environment', flush=True)
            conversation = await client.start_conversation(agent=agent, cwd=work,
                settings={'model': args.model, 'access': {'permission_mode': 'deny'}}, operation_id='live-start')
            report['adapter_version'] = (await conversation.capabilities())['adapter_version']
            reference = conversation.reference
            sent = await conversation.send('Reply with exactly CM_OPENCODE_LIVE_OK. Do not run commands, read files, or use tools.',
                operation_id='live-basic')
            await conversation.disconnect()
            conversation = await ConversationClient(client.home).attach_conversation(reference)
            basic = await done(conversation, sent['result']['turn_id'])
            basic['same_conversation'] = conversation.reference == reference
            basic['history_entries'] = len((await conversation.history())['entries'])
            basic['passed'] = basic['result']['status'] == 'completed' and 'CM_OPENCODE_LIVE_OK' in (basic['result']['final_text'] or '')
            report['checks']['conversation_reconnect'] = basic
            print('Conversation/reconnect: ' + str(basic['passed']), flush=True)
            if not basic['passed']:
                return report
            commands = await conversation.list_slash_commands(available_only=True)
            report['review_advertised'] = any(row['name'] == 'review' and row['executable'] for row in commands['commands'])
            if not report['review_advertised']:
                raise RuntimeError('The live environment did not advertise qualified review')
            sent = await conversation.command('review', 'HEAD', operation_id='live-review')
            review = await done(conversation, sent['result']['turn_id'])
            review['passed'] = review['result']['status'] == 'completed' and bool(review['result']['final_text'])
            report['checks']['review'] = review
            print('Review: ' + str(review['passed']), flush=True)
            return report
        except Exception as error:
            report['error'] = error.to_dict() if isinstance(error, ConversationError) else dict(type=type(error).__name__, message=str(error))
            return report
        finally:
            if conversation:
                await conversation.disconnect()
            try:
                stopped = await client.stop_startup('live-start', operation_id='live-cleanup')
                report['cleanup'] = dict(status=stopped['status'], stopped=(stopped.get('result') or {}).get('stopped'))
            except ConversationError as error:
                report['cleanup'] = dict(status='unconfirmed', error=error.code)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2) + '\n')
            args.output.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-live', action='store_true', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    passed = (not report.get('error') and len(report['checks']) == 2
        and all(check['passed'] for check in report['checks'].values())
        and report.get('cleanup', {}).get('status') == 'completed')
    error = report.get('error') or next((check['result'].get('error') for check in report['checks'].values()
        if not check['passed'] and check['result'].get('error')), None)
    print(json.dumps(dict(passed=passed, output=str(args.output),
        checks={key: value['passed'] for key, value in report['checks'].items()}, error=error)))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
