"""Offline contract coverage; native CLI and model execution are never needed."""
from contextlib import ExitStack, asynccontextmanager
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
import uuid

from agent_manager.errors import Error
from agent_manager.interfaces.sdk import Client
from agent_manager.manager import Manager
from agent_manager.profiles.operations import delete_profile
from agent_manager.profiles.sessions import continue_session, find, remember, listing
from agent_manager.runtime.execution import launch_home
from agent_manager.runtime.native import codex_continuation as adapter
from agent_manager.runtime.native import codex_continue as continuation_process
from agent_manager.runtime.native.codex_continue import allowed, shared_writer
from agent_manager.runtime.native.codex_history import discover_home


@unittest.skipUnless(os.name == 'posix', 'Native writer-lock fixtures require POSIX')
class CodexProfileContinuationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name)
        self.config = self.base / 'providers.toml'
        self.config.write_text('')
        self.manager = Manager('codex', config=self.config, root=self.base / 'state')
        self.manager.new_profile('work'); self.manager.new_profile('backup')
        spec = self.manager.provider_spec
        self.manager.provider_spec = replace(spec, sessions=replace(spec.sessions, cross_environment_resume=True))
        self.source = self.manager.home('work')
        self.target = self.manager.home('backup')
        self.thread = str(uuid.uuid4())
        (self.source / 'sessions').mkdir()
        self.rollout = self.source / 'sessions' / 'original.jsonl'
        self.rollout.write_text(json.dumps({'type': 'session_meta', 'payload': {
            'id': self.thread, 'cwd': str(self.base)}}) + '\n')
        self.contents = self.rollout.read_bytes()
        (self.source / adapter.LOCKS).mkdir()
        (self.source / adapter.LOCKS / '.coordination.lock').touch()
        with sqlite3.connect(self.source / 'state_5.sqlite') as db:
            db.execute('CREATE TABLE threads(id TEXT,cwd TEXT,updated_at INTEGER,rollout_path TEXT)')
            db.execute('INSERT INTO threads VALUES(?,?,?,?)', (self.thread, str(self.base), 10, str(self.rollout)))
            db.execute('CREATE TABLE backfill_state(id INTEGER PRIMARY KEY,status TEXT)')
            db.execute("INSERT INTO backfill_state VALUES(1,'complete')")
        self.saved = remember(self.manager, 'work', self.thread, cwd=self.base)
        self.dependencies = ExitStack()
        self.addCleanup(self.dependencies.close)
        for name, value in (('available', True), ('_native_version', Path('/fixture/codex'))):
            self.dependencies.enter_context(patch.object(adapter, name, return_value=value))
        self.dependencies.enter_context(patch.object(adapter.shutil, 'which', return_value='/fixture/bwrap'))

    def prepare(self):
        return adapter.prepare(self.manager, self.target, self.saved,
            args=['resume', self.thread], cwd=self.base)

    def test_preparation_preserves_native_files_and_original_history(self):
        for home, marker in ((self.source, 'source-placeholder'), (self.target, 'target-placeholder')):
            (home / 'auth.json').write_text(marker)
        view = self.prepare()
        self.assertEqual(view.destination, self.target)
        self.assertEqual(self.rollout.read_bytes(), self.contents)
        self.assertEqual((self.source / 'auth.json').read_text(), 'source-placeholder')
        self.assertEqual((self.target / 'auth.json').read_text(), 'target-placeholder')
        self.assertFalse((self.target / 'sessions').exists())
        self.assertFalse(any(self.target.glob('state_*.sqlite')))
        self.assertFalse(any(path.is_symlink() for path in self.target.rglob('*')))

    def test_command_shares_the_native_lock_directory_and_original_history_store(self):
        view = self.prepare()
        argv = view.command(self.manager, ['codex', '-c', 'developer_instructions="captured"',
            '--sandbox', 'read-only', '--model', 'chosen-model', 'resume', self.thread])
        mounts = [argv[index + 1:index + 3] for index, item in enumerate(argv) if item == '--bind']
        self.assertEqual(mounts, [[str(self.source / adapter.LOCKS), str(self.target / adapter.LOCKS)]])
        descriptor = json.loads(Path(argv[-1]).read_text())
        self.assertEqual(descriptor['rollout'], str(self.rollout))
        self.assertEqual(descriptor['home'], str(self.target))
        self.assertEqual(descriptor['history_home'], str(self.source))
        self.assertEqual(descriptor['model'], 'chosen-model')
        self.assertIn('developer_instructions="captured"', descriptor['server_args'])
        self.assertIn('sandbox_mode="read-only"', descriptor['server_args'])
        self.assertNotIn('sqlite_home=' + json.dumps(str(self.source)), descriptor['server_args'])

    def test_continuation_uses_the_selected_native_profile_configuration(self):
        view = self.prepare()
        source_config = 'sandbox_mode="danger-full-access"\napproval_policy="never"\n'
        target_config = 'sandbox_mode="read-only"\napproval_policy="on-request"\n'
        (self.source / 'config.toml').write_text(source_config)
        (self.target / 'config.toml').write_text(target_config)
        argv = view.command(self.manager, ['codex', *self.manager.launch_args('backup', ['resume', self.thread])])
        descriptor = json.loads(Path(argv[-1]).read_text())
        self.assertEqual(descriptor['home'], str(self.target))
        self.assertEqual(descriptor['rollout'], str(self.rollout))
        self.assertEqual(descriptor['server_args'], [])
        self.assertEqual((self.source / 'config.toml').read_text(), source_config)
        self.assertEqual((self.target / 'config.toml').read_text(), target_config)

    def test_explicit_bypass_flags_preserve_policy_in_the_owned_native_server(self):
        view = self.prepare()
        for flag in ('--yolo', '--dangerously-bypass-approvals-and-sandbox'):
            with self.subTest(flag=flag):
                self.config.write_text('[providers.codex]\nlaunch_args = ' + json.dumps([flag]) + '\n')
                manager = Manager('codex', config=self.config, root=self.base / 'state')
                command = view.command(manager, ['codex', *manager.launch_args('backup', ['resume', self.thread])])
                descriptor = json.loads(Path(command[-1]).read_text())
                self.assertEqual(descriptor['server_args'], ['-c', 'approval_policy="never"',
                    '-c', 'sandbox_mode="danger-full-access"'])
                self.assertEqual(descriptor['home'], str(self.target))
                self.assertNotIn(flag, descriptor['server_args'])

    def test_unknown_option_error_names_the_flag_without_its_value(self):
        with self.assertRaisesRegex(Error, '--unknown-option') as caught:
            adapter._server_arguments(['codex', '--unknown-option=private-value', 'resume', self.thread], self.thread, self.base)
        self.assertNotIn('private-value', str(caught.exception))

    def test_process_failure_retains_the_exception_in_private_diagnostics(self):
        descriptor = self.base / 'launch.json'
        diagnostics = self.base / 'native-server.log'
        diagnostics.write_text('Original native diagnostic\n')
        diagnostics.chmod(0o600)
        stderr = io.StringIO()
        with patch.object(continuation_process.sys, 'argv', ['codex_continue', str(descriptor)]), \
                patch.object(continuation_process, 'supervise', new=AsyncMock(side_effect=RuntimeError('private-native-payload'))), \
                patch.object(continuation_process.sys, 'stderr', stderr):
            self.assertEqual(continuation_process.main(), 1)
        text = diagnostics.read_text()
        self.assertIn('Original native diagnostic', text)
        self.assertIn('RuntimeError: private-native-payload', text)
        self.assertNotIn('private-native-payload', stderr.getvalue())
        self.assertIn(str(diagnostics), stderr.getvalue())

    def test_process_failure_creates_owner_only_diagnostics(self):
        descriptor = self.base / 'launch.json'
        stderr = io.StringIO()
        with patch.object(continuation_process.sys, 'argv', ['codex_continue', str(descriptor)]), \
                patch.object(continuation_process, 'supervise', new=AsyncMock(side_effect=RuntimeError('fixture failure'))), \
                patch.object(continuation_process.sys, 'stderr', stderr):
            self.assertEqual(continuation_process.main(), 1)
        diagnostics = self.base / 'native-server.log'
        self.assertEqual(diagnostics.stat().st_mode & 0o777, 0o600)

    def test_busy_native_source_fails_without_history_or_target_changes(self):
        import fcntl
        lock = self.source / adapter.LOCKS / (self.thread + '.lock')
        with lock.open('ab') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            with self.assertRaisesRegex(Error, 'still open'): self.prepare()
        self.assertEqual(self.rollout.read_bytes(), self.contents)
        self.assertFalse((self.target / adapter.LOCKS).exists())

    def test_unsupported_version_fails_before_creating_mount_points(self):
        with patch.object(adapter, '_native_version', side_effect=Error('unsupported version')):
            with self.assertRaisesRegex(Error, 'unsupported version'): self.prepare()
        self.assertFalse((self.target / adapter.LOCKS).exists())

    def test_paginated_history_uses_its_original_database_store(self):
        with sqlite3.connect(self.source / 'state_5.sqlite') as db:
            db.execute('ALTER TABLE threads ADD COLUMN history_mode TEXT')
            db.execute("UPDATE threads SET history_mode='paginated'")
        self.assertEqual(discover_home(self.source, 'work')[0].history_mode, 'paginated')
        (self.source / 'thread_history_1.sqlite').touch()
        view = self.prepare()
        command = view.command(self.manager, ['codex', 'resume', self.thread])
        descriptor = json.loads(Path(command[-1]).read_text())
        self.assertEqual(descriptor['history_home'], str(self.source))
        self.assertEqual(descriptor['home'], str(self.target))
        self.assertFalse(any(self.target.glob('*.sqlite')))
        self.assertEqual(self.rollout.read_bytes(), self.contents)
        with patch.object(self.manager, '_run_native', return_value=0) as native:
            self.assertEqual(continue_session(self.manager, self.saved['ref'], profile='work'), 0)
        self.assertEqual(native.call_args.args[0], 'work')

    def test_paginated_history_in_native_session_metadata_is_detected(self):
        metadata = json.loads(self.rollout.read_text())
        metadata['payload']['history_mode'] = 'paginated'
        self.rollout.write_text(json.dumps(metadata) + '\n')
        with self.assertRaisesRegex(Error, 'paginated history database is unavailable'):
            self.prepare()
        self.assertFalse((self.target / adapter.LOCKS).exists())
        (self.source / 'thread_history_1.sqlite').touch()
        self.assertEqual(self.prepare().source, self.source)

    def test_incomplete_source_index_is_not_backfilled_from_the_destination(self):
        with sqlite3.connect(self.source / 'state_5.sqlite') as db:
            db.execute("UPDATE backfill_state SET status='pending'")
        with self.assertRaisesRegex(Error, 'Finish native Codex history indexing'): self.prepare()
        self.assertFalse((self.target / adapter.LOCKS).exists())

    def test_paginated_descendant_retains_its_database_home_with_an_external_rollout(self):
        external = self.target / 'sessions'; external.mkdir()
        rollout = external / 'descendant.jsonl'
        rollout.write_bytes(self.contents)
        with sqlite3.connect(self.source / 'state_5.sqlite') as db:
            db.execute('ALTER TABLE threads ADD COLUMN history_mode TEXT')
            db.execute("UPDATE threads SET history_mode='paginated',rollout_path=?", (str(rollout),))
        (self.source / 'thread_history_1.sqlite').touch()
        self.rollout.unlink()
        rows = discover_home(self.source, 'work')
        self.assertEqual(rows[0].home, self.source)
        self.assertEqual(rows[0].rollout, rollout)
        self.assertEqual(self.prepare().rollout, rollout)

    def test_paginated_stub_without_its_database_is_not_an_empty_session_copy(self):
        metadata = json.loads(self.contents)
        metadata['payload']['history_mode'] = 'paginated'
        external = self.target / 'sessions'; external.mkdir()
        (external / 'stub.jsonl').write_text(json.dumps(metadata) + '\n')
        self.assertEqual(discover_home(self.target, 'backup'), [])

    def test_purge_preserves_an_external_rollout_referenced_by_a_source_store(self):
        external = self.target / 'sessions'; external.mkdir()
        rollout = external / 'descendant.jsonl'; rollout.write_bytes(self.contents)
        remember(self.manager, 'work', 'descendant', cwd=self.base,
            history_home=self.source, rollout_path=rollout, history_mode='paginated')
        identifier = self.manager.resolve_profile('backup').environment_id
        delete_profile(self.manager, 'backup')
        with self.assertRaisesRegex(Error, 'Retained sessions'): self.manager.purge_environment(identifier)
        self.assertTrue(rollout.exists())

    def test_purge_checks_native_descendants_before_registry_discovery(self):
        external = self.target / 'sessions'; external.mkdir()
        rollout = external / 'descendant.jsonl'; rollout.write_bytes(self.contents)
        with sqlite3.connect(self.source / 'state_5.sqlite') as db:
            db.execute('INSERT INTO threads VALUES(?,?,?,?)',
                ('not-yet-discovered', str(self.base), 11, str(rollout)))
            # Native session IDs are UUIDs; this one isn't in Manager's registry.
            db.execute('UPDATE threads SET id=? WHERE id=?', (str(uuid.uuid4()), 'not-yet-discovered'))
        identifier = self.manager.resolve_profile('backup').environment_id
        delete_profile(self.manager, 'backup')
        with self.assertRaisesRegex(Error, 'Native history'): self.manager.purge_environment(identifier)
        self.assertTrue(rollout.exists())

    def test_purge_preserves_archived_native_descendants(self):
        archived = self.target / 'archived_sessions'; archived.mkdir()
        rollout = archived / 'descendant.jsonl'; rollout.write_bytes(self.contents)
        with sqlite3.connect(self.source / 'state_5.sqlite') as db:
            db.execute('ALTER TABLE threads ADD COLUMN archived INTEGER DEFAULT 0')
            db.execute('INSERT INTO threads VALUES(?,?,?,?,1)',
                (str(uuid.uuid4()), str(self.base), 11, str(rollout)))
        self.assertEqual(len(discover_home(self.source, 'work')), 1)
        identifier = self.manager.resolve_profile('backup').environment_id
        delete_profile(self.manager, 'backup')
        with self.assertRaisesRegex(Error, 'Native history'):
            self.manager.purge_environment(identifier)
        self.assertTrue(rollout.exists())

    def test_bootstrap_selects_source_sqlite_and_destination_home_before_resuming(self):
        import asyncio
        view = self.prepare()
        command = view.command(self.manager, ['codex', 'resume', self.thread])
        descriptor = Path(command[-1])
        launch_data = json.loads(descriptor.read_text())
        os.link(launch_data['source_lock'], launch_data['target_lock'])
        native = Mock()
        native.poll.return_value = None
        native.wait.return_value = 0
        requests = []
        class Connection:
            async def __aenter__(self): return self
            async def __aexit__(self, *_): pass
            async def request(inner, method, params):
                requests.append((method, params))
                if method == 'config/read': return {'config': {'sqlite_home': str(self.source)}}
                return {'thread': {'id': self.thread, 'path': str(self.rollout), 'cwd': str(self.base)}}
        @asynccontextmanager
        async def endpoint(*_): yield
        async def inline(function, *args, **kwargs): return function(*args, **kwargs)
        tui = Mock(returncode=0)
        tui.wait = AsyncMock(return_value=0)
        with patch.dict(os.environ, {'CODEX_HOME':str(self.target)}), \
                patch('agent_manager.runtime.native.codex_wire.NativeCodexConnection', return_value=Connection()), \
                patch.object(continuation_process.subprocess, 'Popen', return_value=native) as launch, \
                patch.object(continuation_process, 'terminal_endpoint', endpoint), \
                patch.object(continuation_process, 'shared_writer') as writer, \
                patch.object(continuation_process.asyncio, 'create_subprocess_exec', new=AsyncMock(return_value=tui)), \
                patch.object(continuation_process.asyncio, 'to_thread', side_effect=inline):
            self.assertEqual(asyncio.run(continuation_process.run(descriptor)), 0)
        self.assertEqual([method for method, _ in requests], ['config/read', 'thread/resume'])
        self.assertEqual(requests[1][1]['threadId'], self.thread)
        self.assertEqual(launch.call_args.kwargs['env']['CODEX_HOME'], str(self.target))
        self.assertEqual(launch.call_args.kwargs['env']['CODEX_SQLITE_HOME'], str(self.source))
        self.assertEqual(launch.call_args.args[0][-2:], ['-c', 'sqlite_home='+json.dumps(str(self.source))])
        native.terminate.assert_called_once()
        writer.assert_called_once()

    def test_bootstrap_rejects_a_native_sqlite_override_before_resuming(self):
        import asyncio
        command = self.prepare().command(self.manager, ['codex', 'resume', self.thread])
        descriptor = Path(command[-1])
        launch_data = json.loads(descriptor.read_text())
        os.link(launch_data['source_lock'], launch_data['target_lock'])
        native = Mock(); native.poll.return_value = None
        requests = []
        class Connection:
            async def __aenter__(self): return self
            async def __aexit__(self, *_): pass
            async def request(inner, method, params):
                requests.append(method)
                return {'config': {'sqlite_home': str(self.target)}}
        async def inline(function, *args, **kwargs): return function(*args, **kwargs)
        with patch.dict(os.environ, {'CODEX_HOME':str(self.target)}), \
                patch('agent_manager.runtime.native.codex_wire.NativeCodexConnection', return_value=Connection()), \
                patch.object(continuation_process.subprocess, 'Popen', return_value=native), \
                patch.object(continuation_process.asyncio, 'create_subprocess_exec', new=AsyncMock()) as terminal, \
                patch.object(continuation_process.asyncio, 'to_thread', side_effect=inline):
            with self.assertRaisesRegex(Error, 'original native SQLite history store'):
                asyncio.run(continuation_process.run(descriptor))
        self.assertEqual(requests, ['config/read'])
        terminal.assert_not_called()
        native.terminate.assert_called_once()

    def test_source_sqlite_redirect_and_destination_id_collision_fail_closed(self):
        config = self.source / 'config.toml'
        config.write_text('sqlite_home="/another/store"')
        with self.assertRaisesRegex(Error, 'redirected'): self.prepare()
        config.unlink()
        (self.target / 'sessions').mkdir()
        (self.target / 'sessions' / 'different.jsonl').write_bytes(self.contents)
        with self.assertRaisesRegex(Error, 'different native conversation'): self.prepare()

    def test_disconnected_origin_can_continue_by_retained_reference(self):
        original_id = self.saved['origin_environment_id']
        delete_profile(self.manager, 'work')
        with patch.object(self.manager, '_run_native', return_value=0) as launch:
            continue_session(self.manager, self.saved['ref'], profile='backup')
        self.assertEqual(launch.call_args.kwargs['_source_session']['origin_environment_id'], original_id)
        self.assertEqual(launch.call_args.kwargs['cwd'], str(self.base))
        self.assertEqual(len(listing(self.manager, refresh=False)), 1)
        self.assertEqual(self.rollout.read_bytes(), self.contents)

    def test_plan_retains_execution_identity_and_blocks_a_second_managed_writer(self):
        with launch_home(self.manager, 'backup', args=['resume', self.thread], cwd=self.base,
                thread_id=self.thread, source_session=self.saved) as plan:
            state = self.manager.state()
            self.assertEqual(plan.home, self.target)
            self.assertEqual(plan.provenance['source_history_home'], str(self.source))
            state['runs']['other'] = {'agent': 'work', 'environment_id': self.manager.resolve_profile('work').environment_id, 'pid': os.getpid(), 'home': str(self.source),
                'thread_id': self.thread}
            with self.assertRaisesRegex(Error, 'active managed run'): plan.claim(self.manager, state, 'backup')

    def test_external_rollout_metadata_keeps_the_original_history_reference(self):
        with sqlite3.connect(self.target / 'state_5.sqlite') as db:
            db.execute('CREATE TABLE threads(id TEXT,cwd TEXT,updated_at INTEGER,rollout_path TEXT)')
            db.execute('INSERT INTO threads VALUES(?,?,?,?)', (self.thread, str(self.base), 11, str(self.rollout)))
        self.assertEqual(discover_home(self.target, 'backup')[0].home, self.source)
        self.assertEqual(find(self.manager, self.thread)['ref'], self.saved['ref'])


    def test_native_proxy_rejects_replacement_and_wrong_thread_requests(self):
        launch = {'thread_id': self.thread}
        self.assertTrue(allowed({'method': 'turn/start', 'params': {'threadId': self.thread}}, launch))
        for method, params in (('thread/start', {}), ('thread/fork', {'threadId': self.thread}),
                ('thread/resume', {'threadId': str(uuid.uuid4())}),
                ('thread/resume', {'threadId': self.thread, 'history': []}),
                ('thread/archive', {'threadId': self.thread})):
            self.assertFalse(allowed({'method': method, 'params': params}, launch))

    def test_terminal_listing_uses_only_the_original_store_without_scanning_target_rollouts(self):
        import asyncio
        forwarded, replies = [], []
        handler = None
        class Native:
            async def send(inner, raw): forwarded.append(json.loads(raw))
            def __aiter__(inner): return inner
            async def __anext__(inner):
                await asyncio.Event().wait()
        class Terminal:
            def __aiter__(inner):
                inner.messages = iter([
                    {'id': 1, 'method': 'thread/list', 'params': {'limit': 7, 'useStateDbOnly': False}},
                    {'id': 2, 'method': 'thread/start', 'params': {}},
                    {'id': 3, 'method': 'thread/items/list', 'params': {'threadId': self.thread}},
                ])
                return inner
            async def __anext__(inner):
                try: return json.dumps(next(inner.messages))
                except StopIteration: raise StopAsyncIteration
            async def send(inner, raw): replies.append(json.loads(raw))
            async def close(inner): pass
        @asynccontextmanager
        async def connect(*_, **kwargs): yield Native()
        server = Mock()
        server.wait_closed = AsyncMock()
        async def serve(peer, *_, **kwargs):
            nonlocal handler
            handler = peer
            return server
        async def exercise():
            async with continuation_process.terminal_endpoint('native.sock', 'terminal.sock', {'thread_id': self.thread}):
                await handler(Terminal())
        with patch('aixand_local_runtime.websocket.connect', connect), \
                patch('aixand_local_runtime.websocket.serve', serve), \
                patch.object(continuation_process.os, 'chmod'):
            asyncio.run(exercise())
        self.assertEqual(forwarded, [
            {'id': 1, 'method': 'thread/list', 'params': {'limit': 7, 'useStateDbOnly': True}},
            {'id': 3, 'method': 'thread/items/list', 'params': {'threadId': self.thread}},
        ])
        self.assertEqual(replies[0]['id'], 2)
        self.assertIn('error', replies[0])
        server.close.assert_called_once()
        server.wait_closed.assert_awaited_once()

    def test_shared_writer_requires_the_same_inode_and_a_native_lock(self):
        import fcntl
        source = self.base / 'source.lock'; source.touch()
        target = self.base / 'mounted.lock'; os.link(source, target)
        coordination = self.base / 'coordination.lock'; coordination.touch()
        launch = {'source_lock': str(source), 'target_lock': str(target), 'coordination': str(coordination)}
        with self.assertRaisesRegex(Error, 'did not acquire'): shared_writer(launch)
        with source.open('rb') as writer:
            fcntl.flock(writer, fcntl.LOCK_EX)
            shared_writer(launch)
        target.unlink(); target.touch()
        with self.assertRaisesRegex(Error, 'inode changed'): shared_writer(launch)

    def test_sdk_continuation_preserves_the_source_instead_of_attaching_to_its_server(self):
        import asyncio
        from tests.support import FakeRuntime
        client = Client('codex', config=self.config, root=self.manager.root.parent, runtime=FakeRuntime())
        client.spawn = AsyncMock(return_value='managed')
        async def inline(function, *args, **kwargs):
            return function(*args, **kwargs)
        with patch('agent_manager.interfaces.sdk.asyncio.to_thread', side_effect=inline):
            result = asyncio.run(client.continue_session(self.saved['ref'], profile='backup'))
        self.assertEqual(result, 'managed')
        options = client.spawn.call_args.kwargs
        self.assertEqual(options['thread_id'], self.thread)
        self.assertEqual(options['_source_session']['history_home'], str(self.source))
        self.assertEqual(options['profile'], 'backup')
        self.assertNotIn('app_server_run_id', options)


class NativeWriterCoordinationTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_coordination_contention_rechecks_evidence(self):
        with patch.object(continuation_process, 'shared_writer', side_effect=[BlockingIOError(), None]) as check, \
                patch.object(continuation_process.asyncio, 'sleep', AsyncMock()) as sleep:
            await continuation_process.verify_writer({'thread_id': 'selected'})
        self.assertEqual(check.call_count, 2)
        sleep.assert_awaited_once()

    async def test_invalid_writer_evidence_is_never_retried(self):
        with patch.object(continuation_process, 'shared_writer', side_effect=Error('changed inode')) as check, \
                patch.object(continuation_process.asyncio, 'sleep', AsyncMock()) as sleep:
            with self.assertRaisesRegex(Error, 'changed inode'):
                await continuation_process.verify_writer({'thread_id': 'selected'})
        check.assert_called_once()
        sleep.assert_not_awaited()

    async def test_coordination_wait_is_bounded(self):
        with patch.object(continuation_process, 'shared_writer', side_effect=BlockingIOError()) as check:
            with self.assertRaises(BlockingIOError):
                await continuation_process.verify_writer({'thread_id': 'selected'}, timeout=0)
        check.assert_called_once()
