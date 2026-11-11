"""Linux native checks for the production file-history adapters.

Run python tests/manual/file_continuation.py [claude|copilot|both]. Claude 2.1.316
uses the production CLI wrapper in a PTY with a fixed offline fixture key loaded
by native settings, and only /rename. Copilot 1.0.105 checks native stdio RPC and
the production wrapper in a PTY with an offline fixture provider and /rename.
Both verify session identity and original history write-back. No prompts, model
turns, real logins, downloads, GitHub mutations or user configuration changes.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
import uuid

sys.path[:0] = [str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parent)]
from agent_manager import Manager
from agent_manager.profiles.sessions import remember, for_resume
from agent_manager.runtime.native.file_continuation import prepare
from claude_resume import NativeTerminal
from copilot_resume import Native, wait_for_events


def fixture(provider):
    program = shutil.which(provider)
    if not program: raise SystemExit('Install the qualified native ' + provider + ' CLI first')
    program = str(Path(program).resolve())
    base = Path(tempfile.mkdtemp(prefix='agent-native-' + provider + '-handoff-'))
    print('Disposable fixture: ' + str(base), flush=True)
    for name in ('source', 'target', 'workspace', 'tmp', 'user'): (base / name).mkdir()
    config = base / 'providers.toml'
    config.write_text(f'[providers.{provider}]\nexecutable=' + json.dumps(program) + '\n')
    manager = Manager(provider, config, base / 'state')
    for name in ('source', 'target'): manager.connect_profile(name, base / name)
    return base, program, manager


def claude():
    base, program, manager = fixture('claude')
    sid, first = str(uuid.uuid4()), str(uuid.uuid4())
    for home in (base / 'source', base / 'target'):
        (home / '.claude.json').write_text(json.dumps(dict(hasCompletedOnboarding=True, theme='dark',
            projects={str(base / 'workspace'): {'hasTrustDialogAccepted': True}})))
    settings = {'env': {'ANTHROPIC_API_KEY': 'sk-ant-offline-disposable-fixture',
        'ANTHROPIC_BASE_URL': 'http://127.0.0.1:9'}}
    (base / 'target/settings.json').write_text(json.dumps(settings))
    project = base / 'source/projects' / re.sub('[^a-zA-Z0-9]', '-', str(base / 'workspace'))
    project.mkdir(parents=True)
    transcript = project / (sid + '.jsonl')
    envelope = dict(sessionId=sid, cwd=str(base / 'workspace'), isSidechain=False, version='2.1.316',
        timestamp=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'))
    rows = [dict(envelope, type='user', uuid=first, parentUuid=None,
        message={'role': 'user', 'content': 'SOURCE_HISTORY_SENTINEL'}),
        dict(envelope, type='assistant', uuid=str(uuid.uuid4()), parentUuid=first,
            message={'id': 'msg_fixture', 'type': 'message', 'role': 'assistant', 'model': 'fixture',
                'content': [{'type': 'text', 'text': 'COMPLETED_SOURCE_REPLY'}],
                'stop_reason': 'end_turn', 'stop_sequence': None, 'usage': {'input_tokens': 1, 'output_tokens': 1}})]
    transcript.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    saved = remember(manager, 'source', sid, cwd=base / 'workspace', rollout_path=transcript)
    view = prepare(manager, 'target', saved, args=['--resume', sid], cwd=base / 'workspace')
    arguments = [program, '--resume', sid, '--setting-sources=user', '--settings', '{}',
        '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--disable-slash-commands', '--tools=',
        '--permission-mode', 'dontAsk']
    command = view.command(manager, arguments)
    inode = transcript.stat().st_ino
    with NativeTerminal(base, program, 'production-wrapper', base / 'target', sid, command=command) as target:
        assert target.session_id() == sid
        target.rename(transcript, 'PRODUCTION_WRAPPER_WRITEBACK')
    assert transcript.stat().st_ino == inode
    assert not list((base / 'target/projects').glob('*/*.jsonl'))
    assert for_resume(manager, 'target', sid)['ref'] == saved['ref']
    result = dict(provider='claude', version='2.1.316', productionWrapper=True,
        sameNativeId=True, originalHistoryWriteback=True, sourceInodePreserved=True,
        destinationConfigHome=True, modelCalls=0, realLogins=0)
    (base / 'findings.json').write_text(json.dumps(result, indent=2)); print(json.dumps(result), flush=True)


def copilot():
    base, program, manager = fixture('copilot'); processes = []
    try:
        source = Native(base, program, 'source', base / 'source'); processes.append(source)
        assert source.call('status.get', {}) == {'version': '1.0.105', 'protocolVersion': 3}
        sid = str(uuid.uuid4())
        options = dict(sessionId=sid, name='production link fixture', workingDirectory=str(base / 'workspace'),
            configDirectory=str(base / 'source'), model='fixture', enableFileHooks=False, enableConfigDiscovery=False,
            disableAllMcpServers=True, provider={'type': 'openai', 'baseUrl': 'http://127.0.0.1:9/v1'})
        assert source.call('session.create', options)['nativeSessionId'] == sid
        source.call('session.log', dict(sessionId=sid, message='SOURCE_HISTORY_SENTINEL', ephemeral=False))
        directory = base / 'source/session-state' / sid; events = directory / 'events.jsonl'
        wait_for_events(events, ['SOURCE_HISTORY_SENTINEL'])
        source.close(); processes.remove(source)
        saved = remember(manager, 'source', sid, cwd=base / 'workspace')
        view = prepare(manager, 'target', saved, args=['--resume', sid], cwd=base / 'workspace')
        assert '.file_continue' in ' '.join(view.command(manager, [program, '--resume', sid]))
        before = events.stat().st_ino
        target = Native(base, program, 'target', base / 'target'); processes.append(target)
        assert target.call('status.get', {}) == {'version': '1.0.105', 'protocolVersion': 3}
        options.pop('name'); options.update(configDirectory=str(base / 'target'), continuePendingWork=False, allowTranscriptRecovery=False)
        resumed = target.call('session.resume', options)
        assert resumed['nativeSessionId'] == sid
        assert Path(resumed['workspacePath']).resolve() == directory.resolve()
        target.call('session.log', dict(sessionId=sid, message='PRODUCTION_LINK_WRITEBACK', ephemeral=False))
        wait_for_events(events, ['SOURCE_HISTORY_SENTINEL', 'PRODUCTION_LINK_WRITEBACK'])
        view.validate(manager)
        assert events.stat().st_ino == before == (base / 'target/session-state' / sid / 'events.jsonl').stat().st_ino
        assert for_resume(manager, 'target', sid)['ref'] == saved['ref']
        target.close(); processes.remove(target)
        registry = {'providers': [{'name': 'fixture', 'type': 'openai', 'baseUrl': 'http://127.0.0.1:9/v1'}],
                    'models': [{'id': 'fixture', 'provider': 'fixture'}]}
        (base / 'target/providers.json').write_text(json.dumps(registry))
        command = view.command(manager, [program, '--resume', sid, '--no-auto-update', '--no-auto-login',
            '--disable-client-telemetry', '--disable-builtin-mcps', '--no-custom-instructions', '--sync-session=off'])
        terminal = NativeTerminal(base, program, 'production-wrapper', base / 'target', sid, command=command,
            extra_env={'COPILOT_OFFLINE': 'true', 'COPILOT_AUTO_UPDATE': 'false', 'COPILOT_DISABLE_KEYTAR': '1', 'USE_TGREP': 'false'})
        try:
            terminal.pump(2)
            assert terminal.process.poll() is None and b'SOURCE_HISTORY_SENTINEL' in terminal.output
            if b'No (Esc)' in terminal.output:
                # Trust only this disposable workspace for this single run.
                os.write(terminal.master, b'\r'); terminal.pump(1)
            os.write(terminal.master, b'/rename PRODUCTION_INTERACTIVE_WRITEBACK'); terminal.pump(.2)
            os.write(terminal.master, b'\r')
            deadline = time.monotonic() + 4
            while 'PRODUCTION_INTERACTIVE_WRITEBACK' not in (directory / 'workspace.yaml').read_text():
                if time.monotonic() > deadline: raise AssertionError('Native interactive rename did not write back')
                terminal.pump(.1)
        finally: terminal.__exit__()
        assert events.stat().st_ino == before
        result = dict(provider='copilot', version='1.0.105', productionSessionLink=True,
            productionWrapper=True, nativeInteractiveResume=True, nativeSdkResume=True,
            sameNativeId=True, originalHistoryWriteback=True, sourceInodePreserved=True,
            destinationConfigHome=True, modelCalls=0, realLogins=0)
        (base / 'findings.json').write_text(json.dumps(result, indent=2)); print(json.dumps(result), flush=True)
    finally:
        for process in reversed(processes): process.close()


if __name__ == '__main__':
    selected = sys.argv[1] if len(sys.argv) > 1 else 'both'
    if sys.platform != 'linux' or selected not in ('claude', 'copilot', 'both'): raise SystemExit('Use Linux and claude, copilot or both')
    if selected in ('claude', 'both'): claude()
    if selected in ('copilot', 'both'): copilot()
