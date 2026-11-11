"""Opt-in local Codex account qualification through the frontend API."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

from conversation_manager import ConversationClient, ConversationError


async def completed(conversation, turn_id, *, timeout=120, deny=False):
    counts = Counter()
    decisions = []
    async with asyncio.timeout(timeout):
        async for event in conversation.events():
            if event.get('turn_id') != turn_id:
                continue
            counts[event['kind']] += 1
            if deny:
                for request in await conversation.pending_requests():
                    # Never grant a permission during qualification.
                    method = request.get('native_method', '')
                    if method.endswith('requestApproval'):
                        response = {'native_result': {'decision': 'decline'}}
                        await conversation.respond(request['request_id'], response,
                            operation_id='decline-' + request['request_id'])
                        decisions.append(method)
            if event['kind'] == 'turn.completed':
                return dict(result=await conversation.turn_result(turn_id),
                    event_counts=dict(counts), declined=decisions)


async def qualify(args):
    report = dict(checked_at=datetime.now(timezone.utc).isoformat(),
        environment_id=args.environment_id, model=args.model, checks={})
    with tempfile.TemporaryDirectory(prefix='cm-codex-live-') as temporary:
        work = Path(temporary) / 'work'
        work.mkdir()
        client = ConversationClient(Path(temporary) / 'state',
            agent_config=args.agent_config, agent_root=args.agent_root)
        conversation = None
        try:
            agents = await client.available_agents(providers=('codex',))
            selected = next(row for row in agents if row['reference']['environment_id'] == args.environment_id)
            print('Starting selected local Codex environment', flush=True)
            conversation = await client.start_conversation(agent=selected['reference'], cwd=work,
                settings={'model': args.model, 'access': {'sandbox': 'read-only', 'approvalPolicy': 'on-request'}},
                operation_id='live-start')
            report['adapter_version'] = (await conversation.capabilities())['adapter_version']
            report['conversation_ref'] = conversation.reference
            sent = await conversation.send('Reply with exactly CM_LIVE_OK. Do not use tools, read files, or run commands.',
                operation_id='live-basic')
            reference = conversation.reference
            await conversation.disconnect()
            conversation = await ConversationClient(client.home).attach_conversation(reference)
            basic = await completed(conversation, sent['result']['turn_id'])
            history = await conversation.history()
            basic['same_conversation'] = conversation.reference == reference
            basic['history_entries'] = len(history['entries'])
            basic['passed'] = (basic['result']['status'] == 'completed'
                and 'CM_LIVE_OK' in (basic['result']['final_text'] or '')
                and basic['same_conversation'] and basic['history_entries'] > 0)
            report['checks']['stream_history_reconnect'] = basic
            print('Basic conversation: ' + str(basic['passed']), flush=True)
            if not basic['passed']:
                return report

            if args.controls:
                sent = await conversation.send('Use your terminal tool to run exactly `sleep 30`, then reply CM_CANCEL_DONE. Do not inspect or modify files.',
                    operation_id='live-cancel-send')
                cancelled = await conversation.cancel(sent['result']['turn_id'], operation_id='live-cancel')
                control = await completed(conversation, sent['result']['turn_id'])
                control['cancel_receipt_status'] = cancelled['status']
                control['passed'] = control['result']['status'] == 'cancelled'
                report['checks']['cancellation'] = control
                print('Cancellation: ' + str(control['passed']), flush=True)

                sent = await conversation.send(
                    'Test a permission denial. Use your terminal tool with sandbox_permissions=require_escalated '
                    'and justification="Conversation Manager permission-denial test" to run exactly '
                    '`printf CM_APPROVAL_PROBE > approval-probe.txt` in the working directory. '
                    'Request approval before execution. If declined, stop and reply CM_APPROVAL_DECLINED. '
                    'Do not use another tool or bypass the denial.', operation_id='live-approval-send')
                approval = await completed(conversation, sent['result']['turn_id'], deny=True)
                approval['file_created'] = (work / 'approval-probe.txt').exists()
                approval['passed'] = bool(approval['declined']) and not approval['file_created']
                report['checks']['approval_denial'] = approval
                print('Approval denial: ' + str(approval['passed']), flush=True)
            return report
        except Exception as error:
            report['error'] = error.to_dict() if isinstance(error, ConversationError) else dict(type=type(error).__name__, message=str(error))
            return report
        finally:
            if conversation is not None:
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
    parser.add_argument('--environment-id', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--agent-config')
    parser.add_argument('--agent-root')
    parser.add_argument('--controls', action='store_true', help='Also test cancellation and deny an approval')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(qualify(args))
    success = (not report.get('error') and bool(report['checks'])
        and all(check['passed'] for check in report['checks'].values())
        and report.get('cleanup', {}).get('status') == 'completed')
    print(json.dumps(dict(passed=success, output=str(args.output),
        checks={name: check['passed'] for name, check in report['checks'].items()}, error=report.get('error'))))
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
