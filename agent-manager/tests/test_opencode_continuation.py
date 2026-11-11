"""Copy-on-continue uses native transfer, independent accounts and revision ancestry."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from agent_manager import Client, Error, Manager
from agent_manager.runtime.continuation.coordinator import prepare
from agent_manager.profiles.operations import delete_profile
from agent_manager.profiles.sessions import continue_session, find, listing, select
from agent_manager.providers.opencode import database
from agent_manager.runtime.native import opencode_transfer as native
from agent_manager.runtime.runs import finish_run, read_run, update_run
from tests.support import FakeRuntime


def transcript(sid, cwd):
    return dict(info=dict(id=sid, title='A conversation', location={'directory': str(cwd)},
        cost=0, tokens={'input': 0, 'output': 0, 'reasoning': 0, 'cache': {'read': 0, 'write': 0}},
        time={'created': 1000, 'updated': 1000}), messages=[
        dict(id='msg_originalUser', type='user', text='Original request', time={'created': 1000}),
        dict(id='msg_originalReply', type='assistant', agent='build', model={'providerID': 'fixture', 'id': 'fixture'},
            content=[{'type': 'text', 'text': 'Original reply'}], time={'created': 1001, 'completed': 1002})])


class CopyFixture:
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='agent-opencode-copy-')
        self.addCleanup(temporary.cleanup); self.base = Path(temporary.name)
        self.cli = self.base / 'opencode'
        self.cli.write_text('#!' + sys.executable + '\n' + (Path(__file__).parent / 'fixtures/opencode_transfer.py').read_text())
        self.cli.chmod(0o700)
        self.config = self.base / 'providers.toml'
        self.config.write_text('[providers.opencode]\nexecutable=' + json.dumps(str(self.cli)) + '\n')
        self.manager = Manager('opencode', self.config, self.base / 'manager')
        for profile in ('source', 'target', 'third'):
            self.manager.new_profile(profile)
            path = database(self.manager, self.manager.home(profile)); path.parent.mkdir(parents=True)
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE credential(value TEXT)')
                db.execute('INSERT INTO credential VALUES(?)', (profile + '-account',))
                db.execute('CREATE TABLE session_v2(id TEXT PRIMARY KEY,title TEXT,time_updated INTEGER,directory TEXT,parent_id TEXT,time_archived INTEGER)')
                db.execute('CREATE TABLE projected(id TEXT PRIMARY KEY,payload TEXT)')
                db.execute('CREATE TABLE session_message(id TEXT PRIMARY KEY,session_id TEXT)')
        self.sid = 'ses_' + uuid.uuid4().hex
        self.write('source', transcript(self.sid, self.base))
        self.saved = find(self.manager, self.sid)

    def write(self, profile, payload):
        info = payload['info']
        with sqlite3.connect(database(self.manager, self.manager.home(profile))) as db:
            db.execute('INSERT OR REPLACE INTO session_v2 VALUES(?,?,?,?,NULL,NULL)',
                (info['id'], info.get('title'), 1000, info['location']['directory']))
            db.execute('INSERT OR REPLACE INTO projected VALUES(?,?)', (info['id'], json.dumps(payload)))
            for message in payload['messages']:
                db.execute('INSERT OR REPLACE INTO session_message VALUES(?,?)', (message['id'], info['id']))

    def payload(self, profile, sid):
        with sqlite3.connect(database(self.manager, self.manager.home(profile))) as db:
            return json.loads(db.execute('SELECT payload FROM projected WHERE id=?', (sid,)).fetchone()[0])

    def observed(self, profile):
        return json.loads(database(self.manager, self.manager.home(profile)).with_suffix('.observed.json').read_text())

    def copy(self, profile='target', source=None):
        self.assertEqual(continue_session(self.manager, source or self.saved['ref'], profile=profile), 0)
        observed = self.observed(profile)
        return find(self.manager, observed['id'])

    def append(self, profile, sid, text):
        payload = self.payload(profile, sid)
        payload['messages'].append(dict(id='msg_' + uuid.uuid4().hex, type='user', text=text, time={'created': 2000}))
        self.write(profile, payload)

    def imports(self, profile):
        path = database(self.manager, self.manager.home(profile)).parent / 'commands.jsonl'
        return [row for row in map(json.loads, path.read_text().splitlines()) if row[:2] == ['session', 'import']] if path.exists() else []


class OpenCodeContinuationTests(CopyFixture, unittest.TestCase):
    def test_cli_logical_conversation_prompts_for_profile_and_launches_the_native_copy(self):
        from agent_manager.interfaces.cli.dispatch import execute
        copied = self.copy()
        self.append('target', copied['native_id'], 'Latest work in target')
        prompts = []
        def picker(title, columns, rows, detail):
            prompts.append(title)
            self.assertEqual(columns, ['Profile', 'Budget', 'Native home'])
            self.assertTrue(all(row[1] == 'Unknown' for row in rows))
            return next(index for index, row in enumerate(rows) if row[0] == 'third')
        with patch('agent_manager.interfaces.cli.tui.pick', picker), \
                patch('agent_manager.interfaces.cli.tui.require_terminal'), \
                patch.object(Manager, 'budget', return_value={'scope': 'provider-connection', 'status': 'unknown'}) as budgets:
            self.assertEqual(execute(['--config', str(self.config), '--home', str(self.manager.root.parent),
                'opencode', 'continue', copied['conversation_id']]), 0)
        self.assertEqual(budgets.call_count, 3)
        self.assertEqual(prompts, ['Choose an execution profile'])
        observed = self.observed('third')
        self.assertEqual(observed['account'], 'third-account')
        self.assertEqual(observed['payload']['messages'][-1]['text'], 'Latest work in target')
        self.assertEqual(len(self.payload('source', self.sid)['messages']), 2)

    def test_capability_exposes_copy_semantics_without_starting_a_provider(self):
        caps = self.manager.provider_spec.capabilities()
        self.assertTrue(caps['crossEnvironmentResume'])
        self.assertEqual(caps['crossEnvironmentResumeMode'], 'copy')
        self.assertEqual(self.imports('source'), [])

    def test_copy_preserves_history_and_destination_account(self):
        source = self.payload('source', self.sid)
        copied = self.copy()
        row = self.observed('target')
        self.assertEqual(row['account'], 'target-account')
        self.assertNotEqual(copied['native_id'], self.sid)
        self.assertEqual(native.revision(source), native.revision(row['payload']))
        self.assertEqual(copied['cwd'], str(self.base))
        self.assertEqual(self.payload('source', self.sid), source)
        self.assertEqual(copied['origin_environment_id'], self.manager.resolve_profile('target').environment_id)
        state = self.manager.state()
        self.assertNotIn('source-account', json.dumps(state)); self.assertNotIn('target-account', json.dumps(state))

    def test_full_screen_copy_keeps_the_saved_session_model(self):
        self.manager.set_model('target', 'fixture/profile-default')
        copied = self.copy()
        row = self.observed('target')
        self.assertEqual(row['args'], ['--standalone', '--session', copied['native_id']])
        self.assertEqual(row['payload']['messages'][1]['model'], dict(providerID='fixture', id='fixture'))
        self.assertEqual(self.manager.state()['models'][self.manager.resolve_profile('target').environment_id], 'fixture/profile-default')

    def test_full_screen_and_mini_validation_require_the_exact_private_session(self):
        for prefix in (['opencode'], ['opencode', 'mini']):
            with self.subTest(prefix=prefix):
                native.validate_arguments([*prefix, '--standalone', '--session', self.sid], self.sid)
                for flags in (['--standalone', '--session', 'ses_wrong'],
                        ['--session', self.sid], ['--standalone', '--session', self.sid, '--continue'],
                        ['--standalone', '--session', self.sid, '--server', 'http://remote']):
                    with self.assertRaises(Error): native.validate_arguments([*prefix, *flags], self.sid)

    def test_equal_revision_reuses_existing_destination_copy(self):
        first = self.copy(); second = self.copy()
        self.assertEqual(first['ref'], second['ref'])
        self.assertEqual(len(self.imports('target')), 1)

    def test_later_history_returns_as_a_new_copy_and_marks_original_stale(self):
        copied = self.copy()
        self.append('target', copied['native_id'], 'Work completed in target')
        returned = self.copy('source')
        self.assertNotEqual(returned['native_id'], self.sid)
        self.assertEqual(len(self.observed('source')['payload']['messages']), 3)
        self.assertEqual(len(self.payload('source', self.sid)['messages']), 2)
        self.assertEqual(find(self.manager, self.saved['ref'])['copy_status'], 'stale')
        self.assertEqual(returned['conversation_id'], copied['conversation_id'])
        self.assertEqual(self.copy('source')['ref'], returned['ref'])

    def test_independent_changes_are_branches_and_explicit_selection_preserves_both(self):
        copied = self.copy()
        self.append('source', self.sid, 'Source branch')
        self.append('target', copied['native_id'], 'Target branch')
        with self.assertRaisesRegex(Error, 'independent branches'):
            prepare(self.manager, select(self.manager, copied['conversation_id'], 'third'))
        new_copy = self.copy('target')
        self.assertNotEqual(new_copy['native_id'], copied['native_id'])
        self.assertEqual(self.payload('target', copied['native_id'])['messages'][-1]['text'], 'Target branch')
        self.assertEqual(self.observed('target')['payload']['messages'][-1]['text'], 'Source branch')
        self.assertEqual(find(self.manager, new_copy['ref'])['copy_status'], 'branch')

    def test_native_changes_are_detected_even_with_unchanged_update_timestamps(self):
        copied = self.copy()
        self.append('target', copied['native_id'], 'Standalone change')
        newest = self.copy('third')
        self.assertNotEqual(newest['history_revision'], copied['history_revision'])
        self.assertEqual(self.observed('third')['payload']['messages'][-1]['text'], 'Standalone change')

    def test_active_runs_are_never_inspected_or_stopped_for_copy(self):
        state = self.manager.state()
        state['runs']['busy'] = dict(agent='source', environment_id=self.manager.resolve_profile('source').environment_id,
            home=str(self.manager.home('source')), thread_id=self.sid, pid=os.getpid())
        self.manager.save(state)
        with patch.object(self.manager, 'active_runs', side_effect=AssertionError('Activity must not be inspected')):
            self.copy()
        self.assertIn('busy', self.manager.state()['runs'])

    def test_unfinished_native_messages_are_left_to_native_export(self):
        payload = self.payload('source', self.sid)
        payload['messages'].append(dict(id='msg_inProgress', type='assistant', time={'created': 3000},
            agent='build', model={'providerID': 'fixture', 'id': 'fixture'}, content=[]))
        self.write('source', payload)
        self.copy()
        self.assertEqual(len(self.observed('target')['payload']['messages']), 2)
        self.assertEqual(len(self.payload('source', self.sid)['messages']), 3)

    def test_destination_collisions_are_preserved_and_message_ids_are_independent(self):
        payload = transcript(self.sid, self.base); payload['messages'][0]['text'] = 'Unrelated local history'
        self.write('target', payload)
        copied = self.copy()
        self.assertNotEqual(copied['native_id'], self.sid)
        self.assertEqual(self.payload('target', self.sid), payload)
        self.assertTrue(set(row['id'] for row in copied_payload(self, copied)).isdisjoint(row['id'] for row in payload['messages']))

    def test_rename_delete_and_name_reuse_during_import_keep_captured_environment(self):
        original_home = self.manager.home('target'); imported = native.import_session
        def rename(manager, home, payload, cwd):
            manager.rename('target', 'company'); delete_profile(manager, 'company'); manager.new_profile('target')
            return imported(manager, home, payload, cwd)
        with patch.object(native, 'import_session', side_effect=rename):
            self.assertEqual(continue_session(self.manager, self.saved['ref'], profile='target'), 0)
        self.assertTrue(database(self.manager, original_home).with_suffix('.observed.json').exists())
        self.assertFalse(database(self.manager, self.manager.home('target')).with_suffix('.observed.json').exists())

    def test_failed_publication_recovers_committed_import_even_after_discovery(self):
        save = self.manager.save
        def fail_complete(state):
            entries = state.get('opencode_continuation', {}).get('transfers', {}).values()
            if any(row['status'] == 'complete' for row in entries): raise OSError('interrupted registry publication')
            return save(state)
        with patch.object(self.manager, 'save', side_effect=fail_complete):
            with self.assertRaises(OSError): self.copy()
        listing(self.manager)  # Discovery may see the committed, unpublished native copy.
        copied = self.copy()
        self.assertEqual(len(self.imports('target')), 1)
        self.assertEqual(copied['copy_status'], 'current')
        self.assertEqual(len(self.manager.state()['opencode_continuation']['conversations']), 1)

    def test_failed_journal_save_imports_nothing(self):
        save = self.manager.save
        def fail_prepared(state):
            if state.get('opencode_continuation', {}).get('transfers'): raise OSError('disk full')
            return save(state)
        with patch.object(self.manager, 'save', side_effect=fail_prepared):
            with self.assertRaises(OSError): self.copy()
        self.assertEqual(self.imports('target'), [])

    def test_native_error_after_commit_is_reconciled_by_export_evidence(self):
        path = database(self.manager, self.manager.home('target')).parent / 'fail-after-import'
        path.touch()
        copied = self.copy()
        self.assertEqual(copied['copy_status'], 'current')
        self.assertEqual(len(self.imports('target')), 1)

    def test_unverified_import_remains_pending_and_is_never_overwritten(self):
        path = database(self.manager, self.manager.home('target')).parent / 'corrupt-import'
        path.touch()
        for _ in range(2):
            with self.assertRaisesRegex(Error, 'did not preserve the selected history'): self.copy()
        self.assertEqual(len(self.imports('target')), 1)
        state = self.manager.state()
        self.assertTrue(all(row['status'] == 'prepared' for row in state['opencode_continuation']['transfers'].values()))
        self.assertEqual(len(state['sessions']), 2)  # Metadata discovery sees the unpublished native import.
        self.assertFalse(any(row.get('conversation_id') for row in state['sessions'].values() if row['native_id'] != self.sid))

    def test_queued_launch_revalidates_the_captured_copy_before_submission(self):
        from agent_manager.runtime.names import _submission
        copied = self.copy()
        state = self.manager.state()
        state['runs']['pending'] = dict(agent='target', environment_id=copied['origin_environment_id'],
            status='prepared', runtime='undercurrent', opencode_copy=copied,
            launch={'command': ['never-run'], 'name': 'copy-fixture'})
        self.manager.save(state)
        with sqlite3.connect(database(self.manager, self.manager.home('target'))) as db:
            db.execute('DELETE FROM session_v2 WHERE id=?', (copied['native_id'],))
        with self.assertRaisesRegex(Error, 'no longer present'): _submission(self.manager, 'pending', set())
        self.assertEqual(self.manager.state()['runs']['pending']['status'], 'prepared')

    def test_unavailable_latest_copy_does_not_silently_resume_old_history(self):
        copied = self.copy(); self.append('target', copied['native_id'], 'Latest turn')
        prepare(self.manager, select(self.manager, copied['ref'], 'target'))
        self.manager.home('target').rename(self.base / 'disconnected-home')
        with self.assertRaisesRegex(Error, 'latest observed OpenCode history is unavailable'):
            self.copy('third')
        self.assertEqual(self.imports('third'), [])

    def test_unqualified_native_version_fails_before_creating_tracking(self):
        self.cli.write_text(self.cli.read_text().replace('v2.0.18', 'v2.0.19'))
        with self.assertRaisesRegex(Error, 'requires native CLI 2.0.18'): self.copy()
        self.assertNotIn('opencode_continuation', self.manager.state())
        self.assertEqual(self.imports('target'), [])

    def test_staged_revert_is_reported_without_importing_a_different_context(self):
        payload = self.payload('source', self.sid)
        payload['info']['revert'] = {'messageID': payload['messages'][0]['id']}
        self.write('source', payload)
        with self.assertRaisesRegex(Error, 'without a staged revert'): self.copy()
        self.assertEqual(self.imports('target'), [])

    def test_disabled_provider_performs_no_transfer_side_effect(self):
        with patch('agent_manager.providers.installation.software.disabled', return_value=True):
            with self.assertRaisesRegex(Error, 'provider is disabled'): self.copy()
        self.assertNotIn('opencode_continuation', self.manager.state())
        self.assertEqual(self.imports('source'), []); self.assertEqual(self.imports('target'), [])

    def test_raw_native_resume_restores_latest_copy_and_workspace(self):
        copied = self.copy(); self.append('target', copied['native_id'], 'Newest')
        self.assertEqual(self.manager._run_native('source', ['--session', self.sid]), 0)
        row = self.observed('source')
        self.assertNotEqual(row['id'], self.sid)
        self.assertEqual(row['cwd'], str(self.base))
        self.assertEqual(row['payload']['messages'][-1]['text'], 'Newest')

    def test_provider_arguments_cannot_fork_or_substitute_retained_copy(self):
        self.manager.config['launch_args'] = ['--fork']
        with self.assertRaisesRegex(Error, 'fork, server or session overrides'): self.copy()
        self.assertFalse(database(self.manager, self.manager.home('target')).with_suffix('.observed.json').exists())

    def test_digest_ignores_native_ids_and_visits_but_preserves_message_content(self):
        payload = transcript(self.sid, self.base)
        payload['messages'].append(dict(id='msg_compaction', type='compaction', status='completed',
            recent='msg_originalUser', summary='References msg_originalUser literally', time={'created': 2000}))
        copied = native.for_import(payload, 'ses_new')
        copied['info']['time']['updated'] = 9000
        copied['info']['time']['viewed'] = 9001
        self.assertEqual(native.revision(payload), native.revision(copied))
        self.assertEqual(copied['messages'][-1]['recent'], copied['messages'][0]['id'])
        self.assertEqual(copied['messages'][-1]['summary'], payload['messages'][-1]['summary'])
        copied['messages'][0]['text'] = 'Different'
        self.assertNotEqual(native.revision(payload), native.revision(copied))


def copied_payload(fixture, copied):
    return fixture.payload('target', copied['native_id'])['messages']


class OpenCodeManagedContinuationTests(CopyFixture, unittest.IsolatedAsyncioTestCase):
    async def test_sdk_continue_and_resume_preserve_copy_workspace(self):
        client = Client('opencode', config=self.config, root=self.base / 'manager', runtime=FakeRuntime())
        def accepted(manager, run_id):
            import subprocess
            row = read_run(manager, run_id)
            result = subprocess.run(row['launch']['command'], cwd=row['launch']['cwd'],
                env=os.environ | row['launch']['env'], capture_output=True, text=True)
            if result.returncode: raise AssertionError(result.stderr)
            update_run(manager, run_id, status='running', session_id='fixture-' + run_id)
        with patch('agent_manager.interfaces.sdk.launch_worker', side_effect=accepted):
            first = await client.continue_session(self.saved['ref'], profile='target')
            copied = find(self.manager, first.thread_id)
            self.assertNotEqual(first.thread_id, self.sid)
            self.assertEqual(self.observed('target')['account'], 'target-account')
            self.assertEqual(read_run(self.manager, first.run_id)['launch']['cwd'], str(self.base))
            finish_run(self.manager, first.run_id, status='finished')
            second = await client.spawn(profile='target', resume_id=copied['native_id'])
            self.assertEqual(second.thread_id, copied['native_id'])
            finish_run(self.manager, second.run_id, status='finished')


    async def test_invalid_sdk_options_never_import_native_history_or_dispatch(self):
        client = Client('opencode', config=self.config, root=self.base / 'manager', runtime=FakeRuntime())
        invalid = [dict(timeout=0), dict(timeout=float('nan')), dict(idempotency_key='not-a-uuid'),
            dict(model=False), dict(env={'TOKEN': None}), dict(native_config={'model': 'unsupported'}),
            dict(args=['--forwarded']), dict(thread_id=str(uuid.uuid4())), dict(resume_id=self.sid),
            dict(metadata={'invalid': {}}), dict(detection='yes')]
        def native_count():
            with sqlite3.connect(database(self.manager, self.manager.home('target'))) as db:
                return db.execute('SELECT COUNT(*) FROM session_v2').fetchone()[0]
        self.assertEqual(native_count(), 0)
        with patch('agent_manager.runtime.continuation.coordinator.prepare', wraps=prepare) as materialize, \
                patch('agent_manager.interfaces.sdk.launch_worker') as worker:
            for auto in (False, True):
                for options in invalid:
                    with self.subTest(auto=auto, options=options), self.assertRaises((Error, ValueError)):
                        await client.continue_session(self.saved['ref'], auto=auto,
                            **({} if auto else {'profile': 'target'}), **options)
            self.assertEqual(native_count(), 0)
            materialize.assert_not_called()
            worker.assert_not_called()

    async def test_stale_revision_and_merged_context_are_checked_before_native_copy(self):
        client = Client('opencode', config=self.config, root=self.base / 'manager', runtime=FakeRuntime())
        inherited = client.with_context(metadata={f'key-{i}': 'value' for i in range(64)})
        with patch('agent_manager.runtime.continuation.coordinator.prepare', wraps=prepare) as materialize:
            with self.assertRaisesRegex(Error, 'Environment changed'):
                await client.continue_session(self.saved['ref'], profile='target', expected_revision='stale')
            with self.assertRaises(Error):
                await inherited.continue_session(self.saved['ref'], profile='target', metadata={'one-more': 'value'})
            with self.assertRaises(Error):
                await client.continue_session(self.saved['ref'], profile='target',
                    labels={f'label-{i}': 'value' for i in range(64)})
            materialize.assert_not_called()


if __name__ == '__main__': unittest.main()
