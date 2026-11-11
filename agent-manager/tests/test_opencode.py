import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock
import urllib.parse

from agent_manager.interfaces.cli.dispatch import execute
from agent_manager.errors import Error
from agent_manager.manager import Manager
from agent_manager.providers import opencode
from agent_manager.interfaces.cli.continuation import continue_session
from agent_manager.profiles.listing import list_profiles
from agent_manager.profiles.selection import select_profile
from agent_manager.interfaces.sdk import Client
from agent_manager.runtime.runs import read_run, update_run


FAKE = r'''#!/usr/bin/env python3
import json, os, pathlib, sqlite3, sys
if sys.argv[1:] == ['--version']:
    print('2.0.18'); raise SystemExit(0)
path = pathlib.Path(os.environ['OPENCODE_DB'])
path.parent.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(path) as db:
    db.execute('CREATE TABLE IF NOT EXISTS credential (id TEXT PRIMARY KEY, integration_id TEXT, label TEXT NOT NULL, value TEXT NOT NULL, connector_id TEXT, method_id TEXT, active INTEGER, time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS session_v2 (id TEXT PRIMARY KEY, title TEXT, time_updated INTEGER, directory TEXT, parent_id TEXT, time_archived INTEGER)')
    if sys.argv[1:3] == ['auth', 'login'] and not os.environ.get('EMPTY_LOGIN'):
        db.execute("INSERT OR REPLACE INTO credential VALUES (?, ?, ?, ?, NULL, NULL, 1, 1, 1)", ('cred_fake', 'provider', 'default', os.environ.get('LOGIN_VALUE', 'fake-token')))
    if sys.argv[1:3] == ['session', 'list']:
        print('[]')
    else:
        path.with_suffix('.observed.json').write_text(json.dumps({'args': sys.argv[1:], 'env': {key: os.environ[key] for key in ('OPENCODE_DB','OPENCODE_CONFIG_DIR','XDG_DATA_HOME','XDG_STATE_HOME','XDG_CACHE_HOME')}, 'config': json.loads(os.environ.get('OPENCODE_CONFIG_CONTENT', '{}'))}))
'''


class OpenCodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.fake = self.base / 'opencode'
        self.fake.write_text(FAKE)
        self.fake.chmod(0o700)
        self.config = self.base / 'providers.toml'
        self.config.write_text('[providers.opencode]\n' +
                               f'executable = {json.dumps(str(self.fake))}\n' +
                               f'native_home = {json.dumps(str(self.base / "native"))}\n')
        self.root = self.base / 'homes'
        self.manager = Manager('opencode', self.config, self.root)
        self.manager.new_profile('default')
        self.manager.set_default('default')
        self.cli = ['--config', str(self.config), '--home', str(self.root), 'opencode']

    def observed(self, profile):
        return json.loads(opencode.database(self.manager, self.manager.home(profile)).with_suffix('.observed.json').read_text())

    def test_profile_model_selection_and_explicit_default_launch(self):
        execute(self.cli + ['profile', 'new', 'free', '--model', 'opencode/space-bunny-free'])
        execute(self.cli + ['run', 'free'])
        self.assertEqual(self.observed('free')['args'], ['--standalone'])
        self.assertEqual(self.observed('free')['config']['model'], 'opencode/space-bunny-free')
        execute(self.cli + ['run', 'free', '--model', 'opencode/longcat-2.5-preview-free'])
        self.assertEqual(self.observed('free')['config']['model'], 'opencode/longcat-2.5-preview-free')
        self.assertEqual(self.manager.state()['models'][self.manager.resolve_profile('free').environment_id], 'opencode/space-bunny-free')
        execute(self.cli + ['run', 'free', '--', 'run', '--model', 'some-provider/future-model', 'hello'])
        self.assertEqual(self.observed('free')['args'], ['run', '--standalone', '--model', 'some-provider/future-model', 'hello'])
        execute(self.cli + ['start'])
        self.assertEqual(self.observed('default')['args'], ['--standalone'])

    def test_full_screen_model_merges_native_config_without_rewriting_files(self):
        original = dict(model='provider/original', plugins=['user-plugin'], instructions=['user.md'],
            mcp=dict(servers=dict(user=dict(type='local', command=['user-tool']))))
        config = self.manager.home('default') / 'config/opencode/opencode.json'
        config.parent.mkdir(parents=True)
        content = '{"model":"provider/persisted"}\n'
        config.write_text(content)
        with patch.dict(os.environ, OPENCODE_CONFIG_CONTENT=json.dumps(original)):
            self.manager._run_native('default', ['--model=provider/selected'])
        observed = self.observed('default')
        self.assertEqual(observed['args'], ['--standalone'])
        self.assertEqual(observed['config'], original | dict(model='provider/selected'))
        self.assertEqual(config.read_text(), content)

    def test_mini_and_noninteractive_run_remain_explicit_native_choices(self):
        self.manager._run_native('default', ['mini'], model='provider/model#high')
        self.assertEqual(self.observed('default')['args'], ['mini', '--standalone', '--model', 'provider/model#high'])
        self.manager._run_native('default', ['run', 'hello'], model='provider/model')
        self.assertEqual(self.observed('default')['args'], ['run', '--standalone', '--model', 'provider/model', 'hello'])

    def test_full_screen_does_not_silently_drop_explicit_variants_or_resume_overrides(self):
        with self.assertRaisesRegex(Error, '#variants'):
            self.manager._run_native('default', [], model='provider/model#high')
        with self.assertRaisesRegex(Error, 'keeps the session model'):
            self.manager._run_native('default', ['--session', 'ses_existing'], model='provider/override')
        self.assertEqual(self.manager.state()['runs'], {})

    def test_full_screen_model_parser_preserves_prompt_values(self):
        command, env = opencode.terminal_launch(['--standalone', '--prompt', '--model=literal',
            '-m', 'provider/selected'], {'OPENCODE_CONFIG_CONTENT': '{"plugins":["user"]}'})
        self.assertEqual(command, ['--standalone', '--prompt', '--model=literal'])
        self.assertEqual(json.loads(env['OPENCODE_CONFIG_CONTENT']), dict(model='provider/selected', plugins=['user']))
        for raw in ('[]', 'invalid'):
            with self.assertRaisesRegex(Error, 'inline configuration'):
                opencode.terminal_launch(['--model', 'provider/model'], {'OPENCODE_CONFIG_CONTENT': raw})

    def test_managed_cli_uses_the_same_full_screen_model_and_profile_home(self):
        from types import SimpleNamespace
        from tests.test_cli_runtime import Runtime, RuntimeError
        from agent_manager.interfaces.cli import terminal
        self.manager.config['runtime'] = 'undercurrent'
        with patch.object(terminal, 'preflight'), \
                patch.object(terminal, 'dependency', return_value=SimpleNamespace(Client=Runtime, UndercurrentError=RuntimeError)), \
                patch('agent_manager.interfaces.sdk.launch_worker', side_effect=lambda manager, run_id:
                    update_run(manager, run_id, session_id='fixture-session', status='running')), \
                patch.object(terminal.subprocess, 'call', return_value=0):
            self.manager._run_native('default', [], model='provider/selected')
        run = next(iter(self.manager.state()['runs'].values()))
        self.assertEqual(run['launch']['command'], [str(self.fake), '--standalone'])
        self.assertEqual(json.loads(run['launch']['env']['OPENCODE_CONFIG_CONTENT'])['model'], 'provider/selected')
        self.assertEqual(run['launch']['env']['OPENCODE_DB'], str(opencode.database(self.manager, self.manager.home('default'))))

    def test_profile_environment_isolated_despite_inherited_settings(self):
        self.manager.new_profile('free')
        with patch.dict(os.environ, OPENCODE_DB='/wrong/database', OPENCODE_CONFIG_DIR='/wrong/config'):
            self.manager._run_native('free', [])
        observed = self.observed('free')['env']
        home = self.manager.home('free')
        self.assertEqual(observed['OPENCODE_DB'], str(home / 'data/opencode/opencode.db'))
        self.assertEqual(observed['OPENCODE_CONFIG_DIR'], str(home / 'config/opencode'))
        self.assertEqual(observed['XDG_CACHE_HOME'], str(home / 'cache'))
        self.assertEqual(observed['XDG_STATE_HOME'], str(home / 'state'))

    def test_direct_launch_replaces_stale_pwd(self):
        with patch.dict(os.environ, PWD='/stale/orchestrator'), patch('agent_manager.manager.subprocess.call', return_value=0) as call:
            self.manager._run_native('default', [])
        self.assertEqual(call.call_args.kwargs['cwd'], Path.cwd().resolve())
        self.assertEqual(call.call_args.kwargs['env']['PWD'], str(Path.cwd().resolve()))

    def test_catalog_uses_target_directory_in_process_environment_and_request(self):
        target = self.base / 'target project'
        target.mkdir()
        process = MagicMock()
        process.stdout = io.StringIO('{"url":"http://127.0.0.1:12345"}\n')
        process.poll.return_value = None
        opener = MagicMock()
        opener.open.return_value.__enter__.side_effect = lambda: io.StringIO('{"data":[{"id":"example"}]}')
        with patch('agent_manager.providers.opencode.subprocess.Popen', return_value=process) as launch, \
                patch('agent_manager.providers.opencode.urllib.request.build_opener', return_value=opener), \
                patch('agent_manager.providers.opencode.time.monotonic', side_effect=[0, 0, 0, 3]), \
                patch('agent_manager.providers.opencode.time.sleep'):
            opencode.catalog(self.manager, self.manager.home('default'), {'PWD': '/wrong'}, cwd=target)
        self.assertEqual(launch.call_args.kwargs['cwd'], target)
        self.assertEqual(launch.call_args.kwargs['env']['PWD'], str(target))
        query = urllib.parse.parse_qs(urllib.parse.urlparse(opener.open.call_args.args[0].full_url).query)
        self.assertEqual(query['location[directory]'], [str(target)])
        process.terminate.assert_called_once()

    def test_catalog_startup_uses_real_pipes_and_cleans_up_on_failure(self):
        # Runs unchanged on Windows; a socket selector cannot read these pipes.
        for output, waits, expected in (
                ('{"url":"http://127.0.0.1:12345"}\\n', True, None),
                ('{"url":', True, 'startup timed out'),
                ('', False, 'Invalid OpenCode'),
                ('{"url":"http://remote.example"}\\n', False, 'Invalid OpenCode'),
                ('x' * 65537, True, 'Invalid OpenCode')):
            with self.subTest(expected=expected, output=output[:40]):
                # Interpret only the fixture newline, without changing JSON.
                output = output.replace('\\n', '\n')
                emitted = "'x' * 65537" if len(output) > 65536 else repr(output)
                source = 'import sys,time; sys.stdout.write(' + emitted + '); sys.stdout.flush(); ' + ('time.sleep(30)' if waits else '')
                process = subprocess.Popen([sys.executable, '-u', '-c', source], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                opener = MagicMock()
                opener.open.return_value.__enter__.side_effect = lambda: io.StringIO('{"data":[{"id":"example"}]}')
                with patch('agent_manager.providers.opencode.subprocess.Popen', return_value=process), \
                        patch('agent_manager.providers.opencode.urllib.request.build_opener', return_value=opener), \
                        patch('agent_manager.providers.opencode.time.monotonic', side_effect=[0, 0, 0, 3]), \
                        patch('agent_manager.providers.opencode.time.sleep'):
                    if expected:
                        with self.assertRaisesRegex(Error, expected):
                            opencode.catalog(self.manager, self.manager.home('default'), {}, cwd=self.base, startup_timeout=1)
                    else:
                        result = opencode.catalog(self.manager, self.manager.home('default'), {}, cwd=self.base, startup_timeout=1)
                        self.assertEqual(result['data'], [{'id': 'example'}])
                self.assertIsNotNone(process.poll())
                self.assertTrue(process.stdout.closed)
                self.assertTrue(process.stdin.closed)
                self.assertFalse(any(thread.name == 'opencode-model-discovery' for thread in threading.enumerate()))

    def test_cli_model_discovery_accepts_cwd_and_rejects_missing_directory(self):
        with patch('agent_manager.providers.opencode.catalog', return_value={'data': []}) as catalog:
            execute(self.cli + ['models', '--cwd', str(self.base)])
        self.assertEqual(catalog.call_args.kwargs['cwd'], self.base)
        with patch('agent_manager.providers.opencode.catalog') as catalog, self.assertRaisesRegex(Error, 'Working directory'):
            self.manager.models(cwd=self.base / 'missing')
        catalog.assert_not_called()

    def test_native_login_and_refresh_stay_in_the_selected_profile_database(self):
        self.manager.new_profile('work')
        self.manager.new_profile('review')
        self.manager.login('work')
        self.manager.login('review')
        path = opencode.database(self.manager, self.manager.home('work'))
        with contextlib.closing(sqlite3.connect(path)) as db, db:
            db.execute('INSERT INTO session_v2 VALUES (?, ?, ?, ?, NULL, NULL)',
                       ('ses_test', 'Keep session', 1000, str(self.base)))
        config = self.manager.home('work') / 'config/opencode/opencode.json'
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text('{"model":"opencode/big-pickle"}')
        with patch.dict(os.environ, LOGIN_VALUE='replacement'):
            self.manager.login('work')
        with contextlib.closing(sqlite3.connect(path)) as db, db:
            self.assertEqual(db.execute('SELECT value FROM credential').fetchone()[0], 'replacement')
            self.assertEqual(db.execute('SELECT title FROM session_v2').fetchone()[0], 'Keep session')
        self.assertIn('big-pickle', config.read_text())
        other = opencode.database(self.manager, self.manager.home('review'))
        with contextlib.closing(sqlite3.connect(other)) as db:
            self.assertEqual(db.execute('SELECT value FROM credential').fetchone()[0], 'fake-token')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM session_v2').fetchone()[0], 0)
        self.assertNotIn('replacement', self.manager.state_path.read_text())
        self.manager._run_native('work', [])
        self.assertEqual(self.manager.state()['runs'], {})

    def test_empty_native_login_is_not_claimed_as_verified_model_access(self):
        self.manager.new_profile('empty')
        with patch.dict(os.environ, EMPTY_LOGIN='1'):
            self.assertEqual(self.manager.login('empty'), 0)
        profile = next(row for row in list_profiles(self.manager) if row['profile'] == 'empty')
        self.assertEqual(profile['login']['state'], 'unknown')
        self.assertEqual(profile['login']['verification'], 'native-command-completed')
        self.assertNotIn('auths', self.manager.state())

    def test_connecting_native_home_keeps_its_database_in_place(self):
        self.manager._invoke_native(self.manager.native, ['auth', 'login', '--standalone'])
        database = self.manager.native / 'opencode.db'
        before = database.read_bytes()
        self.manager.connect_profile('native', self.manager.native)
        self.assertEqual(opencode.database(self.manager, self.manager.home('native')), database)
        self.assertEqual(database.read_bytes(), before)

    def test_explicit_default_and_model_survive_profile_rename(self):
        self.manager.new_profile('free', model='opencode/big-pickle')
        with patch('agent_manager.providers.opencode.subprocess.Popen') as query, \
                patch.object(self.manager, 'budget', return_value=dict(scope='provider-connection', status='unknown')):
            rows = list_profiles(self.manager)
            self.assertEqual(select_profile(self.manager)['profile'], 'default')
        query.assert_not_called()
        self.assertEqual(rows[1]['model'], 'opencode/big-pickle')
        self.manager.rename('free', 'renamed')
        self.assertEqual(self.manager.state()['models'], {self.manager.resolve_profile('renamed').environment_id: 'opencode/big-pickle'})
        execute(self.cli + ['profile', 'model', 'renamed', '--clear'])
        self.assertEqual(self.manager.state()['models'], {})
        execute(self.cli + ['start', '--model', 'opencode/big-pickle'])
        self.assertEqual(self.observed('default')['config']['model'], 'opencode/big-pickle')

    def test_model_discovery_uses_costs_not_a_hardcoded_name_list(self):
        def entry(identifier, costs, enabled=True):
            return {'providerID': 'opencode', 'id': identifier, 'cost': costs, 'enabled': enabled}
        zero = {'input': 0, 'output': 0, 'cache': {'read': 0, 'write': 0}}
        payload = {'data': [entry('new-free-model', [zero]), entry('big-pickle', [zero]),
                            entry('unknown-price', []), entry('paid', [{'input': 1, 'output': 2}]),
                            entry('tiered', [zero, {'input': 0, 'output': 1}]),
                            entry('disabled', [zero], False)]}
        with patch('agent_manager.providers.opencode.catalog', return_value=payload), contextlib.redirect_stdout(io.StringIO()) as output:
            execute(self.cli + ['models', '--free', '--json'])
        self.assertEqual([row['id'] for row in json.loads(output.getvalue())['models']],
                         ['opencode/big-pickle', 'opencode/new-free-model'])

    def test_model_discovery_uses_the_selected_native_home_without_copying_credentials(self):
        self.manager.new_profile('work')
        self.manager.login('work')
        path = opencode.database(self.manager, self.manager.home('work'))
        with contextlib.closing(sqlite3.connect(path)) as db, db:
            db.execute("UPDATE credential SET value = 'live-refresh'")
        def catalog(manager, home, overrides, *, cwd, **options):
            self.assertEqual(cwd, self.base)
            self.assertEqual(home, manager.home('work'))
            self.assertEqual(overrides, {})
            return {'data': []}
        with patch('agent_manager.providers.opencode.catalog', side_effect=catalog):
            self.manager.models('work', cwd=self.base)
        with contextlib.closing(sqlite3.connect(path)) as db, db:
            self.assertEqual(db.execute('SELECT value FROM credential').fetchone()[0], 'live-refresh')

    def test_continuation_restores_native_id_in_original_home(self):
        self.manager.new_profile('work')
        self.manager._run_native('work', [])
        path = opencode.database(self.manager, self.manager.home('work'))
        with contextlib.closing(sqlite3.connect(path)) as db, db:
            db.execute('INSERT INTO session_v2 VALUES (?, ?, ?, ?, NULL, NULL)',
                       ('ses_test123', 'Title', 1000, str(self.base)))
        prompts = []
        def picker(title, *args):
            prompts.append(title)
            return 1 if title == 'Choose an execution profile' else 0
        with patch('agent_manager.interfaces.cli.continuation.Path.cwd', return_value=self.base):
            continue_session(self.manager, [], picker=picker)
        self.assertEqual(prompts, ['Choose a native session', 'Choose an execution profile'])
        self.assertEqual(self.observed('work')['args'], ['--standalone', '--session', 'ses_test123'])

    def test_invalid_models_and_remote_server_fail_before_leasing(self):
        for args, model in (([], 'invalid'), (['--server', 'http://remote'], None),
                            (['--model', 'provider/one'], 'provider/two')):
            with self.assertRaises(Error):
                self.manager._run_native('default', args, model=model)
        self.assertEqual(self.manager.state()['runs'], {})


class OpenCodeSDKTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        OpenCodeTests.setUp(self)
        # These tests verify SDK intent and preparation, not thread scheduling.
        async def inline(function, *args, **kwargs): return function(*args, **kwargs)
        mocked = patch('agent_manager.interfaces.sdk.asyncio.to_thread', side_effect=inline)
        mocked.start()
        self.addCleanup(mocked.stop)

    async def test_sdk_model_and_environment_match_cli(self):
        class Runtime:
            connection_options = {'home': '/tmp/unused-runtime'}
            async def connect(self): pass
        client = Client('opencode', config=self.config, root=self.root, runtime=Runtime())
        await client.create_profile('work', model='opencode/space-bunny-free')
        def accepted(manager, run_id):
            update_run(manager, run_id, session_id='runtime-session', status='running')
        with patch('agent_manager.interfaces.sdk.launch_worker', side_effect=accepted):
            agent = await client.spawn(profile='work', model='opencode/longcat-2.5-preview-free',
                                       cwd=self.base,
                                       env={'OPENCODE_DB': '/wrong/database', 'PWD': '/stale/orchestrator'})
        record = read_run(client.manager, agent.run_id)
        self.assertEqual(record['launch']['command'], [str(self.fake), '--standalone'])
        self.assertEqual(json.loads(record['launch']['env']['OPENCODE_CONFIG_CONTENT'])['model'], 'opencode/longcat-2.5-preview-free')
        self.assertEqual(record['launch']['env']['OPENCODE_DB'], str(Path(agent.home) / 'data/opencode/opencode.db'))
        self.assertEqual(record['launch']['cwd'], str(self.base))
        self.assertEqual(record['launch']['env']['PWD'], str(self.base))

    async def test_sdk_discovery_uses_requested_project(self):
        client = Client('opencode', config=self.config, root=self.root, runtime=object())
        with patch('agent_manager.providers.opencode.catalog', return_value={'data': []}) as catalog:
            self.assertEqual((await client.models(cwd=self.base, free=True))['models'], [])
        self.assertEqual(catalog.call_args.kwargs['cwd'], self.base)
