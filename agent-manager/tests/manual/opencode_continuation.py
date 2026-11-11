"""Host check: python tests/manual/opencode_continuation.py.

Uses the production transfer adapter and OpenCode 2.0.18 with disposable homes,
synthetic history and metadata-only native updates. No login or model call.
Requires permission to start OpenCode's local standalone HTTP server.
"""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent_manager import Error, Manager
from agent_manager.runtime.continuation.coordinator import prepare
from agent_manager.profiles.sessions import find, select
from agent_manager.runtime.native import opencode_transfer as native


def main():
    program = shutil.which('opencode')
    if not program: raise SystemExit('Install OpenCode ' + native.VERSION + ' first')
    base = Path(tempfile.mkdtemp(prefix='agent-native-opencode-copy-'))
    print('Disposable fixture: ' + str(base), flush=True)
    for name in ('workspace', 'user'): (base / name).mkdir()
    config = base / 'providers.toml'
    config.write_text('[providers.opencode]\nexecutable=' + json.dumps(str(Path(program).resolve())) + '\n')
    variables = {key: os.environ[key] for key in ('PATH', 'LANG', 'SYSTEMROOT') if key in os.environ}
    variables.update(HOME=str(base / 'user'), USERPROFILE=str(base / 'user'), TMPDIR=str(base))
    with patch.dict(os.environ, variables, clear=True):
        manager = Manager('opencode', config, base / 'state')
        for name in ('source', 'target', 'third'): manager.new_profile(name)
        native.require_version(manager)
        seed = json.loads(native._invoke(manager, manager.home('source'), base / 'workspace',
            ['api', '--standalone', 'POST', '/api/session', '--data',
             json.dumps({'title': 'Fixture schema seed', 'location': {'directory': str(base / 'workspace')}})]))['data']
        sid = 'ses_' + uuid.uuid4().hex
        payload = dict(info=dict(seed, id=sid, title='Native copy fixture'), messages=[
                dict(id='msg_' + uuid.uuid4().hex, type='user', text='SOURCE_HISTORY_SENTINEL',
                    files=[], agents=[], skills=[], time={'created': 1000}),
                dict(id='msg_' + uuid.uuid4().hex, type='synthetic', text='COMPLETED_SOURCE_HISTORY', time={'created': 1001})])
        native.import_session(manager, manager.home('source'), payload, base / 'workspace')
        original = find(manager, sid)
        copied = dict(prepare(manager, select(manager, original['ref'], 'target')).session)
        source = native.export_session(manager, manager.home('source'), sid, base / 'workspace')
        target = native.export_session(manager, manager.home('target'), copied['native_id'], base / 'workspace')
        assert native.revision(source) == native.revision(target)
        assert copied['native_id'] != sid
        equal = dict(prepare(manager, select(manager, original['ref'], 'target')).session)
        assert equal['native_id'] == copied['native_id']

        def rename(profile, identifier, title):
            native._invoke(manager, manager.home(profile), base / 'workspace', ['api', '--standalone',
                'PATCH', '/api/session/' + identifier, '--data', json.dumps({'title': title})])

        rename('target', copied['native_id'], 'TARGET_NATIVE_UPDATE')
        returned = dict(prepare(manager, select(manager, original['ref'], 'source')).session)
        assert returned['native_id'] != sid
        assert native.export_session(manager, manager.home('source'), sid, base / 'workspace') == source
        assert native.export_session(manager, manager.home('source'), returned['native_id'], base / 'workspace')['info']['title'] == 'TARGET_NATIVE_UPDATE'
        assert find(manager, original['ref'])['copy_status'] == 'stale'
        try: native.import_session(manager, manager.home('target'), target, base / 'workspace')
        except Error: pass
        else: raise AssertionError('Native import unexpectedly overwrote an existing session')

        rename('source', sid, 'SOURCE_BRANCH')
        rename('target', copied['native_id'], 'TARGET_BRANCH')
        try: prepare(manager, select(manager, copied['conversation_id'], 'third'))
        except Error as error: assert 'independent branches' in str(error)
        else: raise AssertionError('Independent branches were merged')
        selected = dict(prepare(manager, select(manager, copied['ref'], 'third')).session)
        assert native.export_session(manager, manager.home('third'), selected['native_id'], base / 'workspace')['info']['title'] == 'TARGET_BRANCH'
        result = dict(provider='opencode', version=native.VERSION, productionAdapter=True,
            nativeExportImport=True, equalCopyReused=True, oldCopiesPreserved=True,
            branchesPreserved=True, originalHistoryUnchanged=True, modelCalls=0, realLogins=0)
        (base / 'findings.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)


if __name__ == '__main__': main()
