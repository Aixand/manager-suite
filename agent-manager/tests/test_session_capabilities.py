"""Profile eligibility and public continuation limits without provider processes."""
import asyncio
from pathlib import Path
import sqlite3
import tempfile
import tomllib
import unittest
from unittest.mock import AsyncMock, Mock, patch

from agent_manager.errors import Error
from agent_manager.interfaces.cli.continuation import continue_session as choose_session
from agent_manager.interfaces.sdk import Client
from agent_manager.manager import Manager
from agent_manager.profiles.operations import delete_profile, inspect_profile
from agent_manager.profiles.sessions import continue_session, execution_profiles, find, listing
from agent_manager.providers import opencode
from agent_manager.providers.schema import ManifestError, parse_definition
from agent_manager.runtime.execution import launch_home


class DisabledSessionCapabilityTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name)
        self.config = self.base / 'providers.toml'
        self.config.write_text('')
        definitions = self.base / 'providers'; definitions.mkdir()
        bundled = Path(__file__).resolve().parents[1] / 'agent_manager/providers/definitions/opencode.toml'
        (definitions / 'opencode.toml').write_text(bundled.read_text().replace(
            'cross_environment_resume = "opencode-session-copy"',
            'cross_environment_resume = "disabled"\ncross_environment_resume_reason = "Session transfer disabled by local policy."'))
        self.manager = Manager('opencode', self.config, self.base / 'state')
        self.manager.new_profile('work')
        self.manager.new_profile('backup')
        self.manager.set_default('backup')
        self.source = self.manager.home('work')
        self.target = self.manager.home('backup')
        for home, marker in ((self.source, 'source-native-placeholder'), (self.target, 'target-native-placeholder')):
            path = opencode.database(self.manager, home)
            path.parent.mkdir(parents=True)
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE session_v2(id TEXT,title TEXT,time_updated INTEGER,directory TEXT,parent_id TEXT,time_archived INTEGER)')
                db.execute('CREATE TABLE credential(value TEXT)')
                db.execute('INSERT INTO credential VALUES(?)', (marker,))
        with sqlite3.connect(opencode.database(self.manager, self.source)) as db:
            db.execute('INSERT INTO session_v2 VALUES(?,?,?,?,NULL,NULL)',
                ('ses_original', 'Original conversation', 1000, str(self.base)))
        self.saved = find(self.manager, 'ses_original')

    def test_capabilities_explain_explicit_local_disable_on_all_profile_views(self):
        capability = self.manager.provider_spec.capabilities()
        self.assertFalse(capability['crossEnvironmentResume'])
        self.assertIn('disabled by local policy', capability['crossEnvironmentResumeReason'])
        self.assertEqual(inspect_profile(self.manager, 'work')['capabilities'], capability)
        self.assertEqual(execution_profiles(self.manager, self.saved), ['work'])

    def test_session_discovery_only_reads_metadata_and_never_credential_values(self):
        connect = sqlite3.connect
        before = {home: opencode.database(self.manager, home).read_bytes() for home in (self.source, self.target)}
        def metadata_only(database_uri, *args, **kwargs):
            self.assertTrue(database_uri.endswith('?mode=ro'))
            db = connect(database_uri, *args, **kwargs)
            def authorize(action, table, *_):
                if table == 'credential' or action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            db.set_authorizer(authorize)
            return db
        with patch.object(opencode.sqlite3, 'connect', side_effect=metadata_only):
            rows = listing(self.manager)
        self.assertEqual([row['native_id'] for row in rows], ['ses_original'])
        for home, contents in before.items():
            self.assertEqual(opencode.database(self.manager, home).read_bytes(), contents)
        registry = self.manager.state_path.read_text()
        self.assertNotIn('source-native-placeholder', registry)
        self.assertNotIn('target-native-placeholder', registry)

    def test_explicit_cross_profile_continue_fails_before_launch_with_the_reason(self):
        before = [opencode.database(self.manager, home).read_bytes() for home in (self.source, self.target)]
        with patch.object(self.manager, '_run_native') as launch:
            with self.assertRaisesRegex(Error, 'disabled by local policy') as caught:
                continue_session(self.manager, self.saved['ref'], profile='backup')
        launch.assert_not_called()
        self.assertIn(str(self.source), str(caught.exception))
        self.assertEqual(before, [opencode.database(self.manager, home).read_bytes() for home in (self.source, self.target)])

    def test_launch_preparation_also_rejects_bypassing_the_selection_gate(self):
        with patch('agent_manager.runtime.native.codex_continuation.prepare') as codex:
            with self.assertRaisesRegex(Error, 'disabled by local policy'):
                with launch_home(self.manager, 'backup', thread_id='ses_original',
                        cwd=self.base, source_session=self.saved):
                    self.fail('An unsupported history home was selected')
        codex.assert_not_called()

    def test_same_home_continue_uses_the_origin_even_when_another_profile_is_default(self):
        with patch.object(self.manager, '_run_native', return_value=0) as launch, patch.object(
                self.manager, 'budget', return_value={'scope': 'provider-connection', 'status': 'unknown'}) as budgets:
            self.assertEqual(continue_session(self.manager, self.saved['ref']), 0)
        self.assertEqual(launch.call_args.args, ('work', ['--session', 'ses_original']))
        self.assertEqual(launch.call_args.kwargs['cwd'], str(self.base))
        budgets.assert_not_called()

    def test_picker_only_offers_the_original_home(self):
        choices = []
        def picker(title, columns, rows, description):
            choices.append((title, rows))
            return 0
        with patch.object(self.manager, '_run_native', return_value=0) as launch, \
                patch('agent_manager.interfaces.cli.continuation.Path.cwd', return_value=self.base), \
                patch.object(self.manager, 'budget', return_value={'scope': 'provider-connection', 'status': 'unknown'}) as budgets:
            self.assertEqual(choose_session(self.manager, picker=picker), 0)
        self.assertEqual(choices[1][1], [('work', 'Unknown', str(self.source))])
        self.assertEqual(budgets.call_count, 1)
        self.assertEqual(launch.call_args.args[0], 'work')

    def test_disconnected_source_reports_its_home_without_offering_an_invalid_profile(self):
        delete_profile(self.manager, 'work')
        picker = Mock(return_value=0)
        with patch.object(self.manager, '_run_native') as launch, \
                patch('agent_manager.interfaces.cli.continuation.Path.cwd', return_value=self.base):
            with self.assertRaisesRegex(Error, 'original history home'):
                choose_session(self.manager, picker=picker)
        picker.assert_called_once()
        launch.assert_not_called()

    def test_reconnected_home_is_eligible_without_changing_session_provenance(self):
        delete_profile(self.manager, 'work')
        self.manager.connect_profile('reconnected', self.source)
        self.assertEqual(execution_profiles(self.manager, self.saved), ['reconnected'])
        with patch.object(self.manager, '_run_native', return_value=0) as launch:
            continue_session(self.manager, self.saved['ref'], profile='reconnected')
        self.assertEqual(launch.call_args.args[0], 'reconnected')
        self.assertEqual(find(self.manager, self.saved['ref'])['origin_environment_id'], self.saved['origin_environment_id'])

    def test_sdk_uses_the_same_rejection_and_preserves_native_resume_in_the_original_home(self):
        from tests.support import FakeRuntime
        client = Client('opencode', config=self.config, root=self.manager.root.parent, runtime=FakeRuntime())
        client.spawn = AsyncMock(return_value='managed')
        async def inline(function, *args, **kwargs): return function(*args, **kwargs)
        with patch('agent_manager.interfaces.sdk.asyncio.to_thread', side_effect=inline):
            with self.assertRaisesRegex(Error, 'disabled by local policy'):
                asyncio.run(client.continue_session(self.saved['ref'], profile='backup'))
            client.spawn.assert_not_called()
            self.assertEqual(asyncio.run(client.continue_session(self.saved['ref'], profile='work')), 'managed')
        self.assertEqual(client.spawn.call_args.kwargs['resume_id'], 'ses_original')
        self.assertEqual(client.spawn.call_args.kwargs['_source_session']['history_home'], str(self.source))

    def test_local_definition_keeps_the_bundled_reason_when_omitted(self):
        bundled = Path(__file__).resolve().parents[1] / 'agent_manager/providers/definitions/antigravity.toml'
        document = bundled.read_text()
        document = '\n'.join(line for line in document.splitlines() if not line.startswith('cross_environment_resume_reason'))
        definitions = self.base / 'providers'
        (definitions / 'antigravity.toml').write_text(document)
        manager = Manager('antigravity', self.config, self.base / 'state')
        native_config = self.base / 'bundled/config.toml'; native_config.parent.mkdir(); native_config.write_text('')
        default = Manager('antigravity', native_config, self.base / 'other-state')
        self.assertEqual(manager.provider_spec.sessions.cross_environment_resume_reason,
            default.provider_spec.sessions.cross_environment_resume_reason)

    def test_manifest_reason_is_a_single_line_only_for_disabled_continuation(self):
        directory = Path(__file__).resolve().parents[1] / 'agent_manager/providers/definitions'
        for reason in (False, '', 'invalid\nreason'):
            document = tomllib.loads((directory / 'opencode.toml').read_text())
            document['terminal']['sessions']['cross_environment_resume_reason'] = reason
            with self.subTest(reason=reason), self.assertRaisesRegex(ManifestError, 'cross_environment_resume_reason'):
                parse_definition(document)
        document = tomllib.loads((directory / 'codex.toml').read_text())
        document['terminal']['sessions']['cross_environment_resume_reason'] = 'Disabled'
        with self.assertRaisesRegex(ManifestError, 'only applies'):
            parse_definition(document)

    def test_codex_platform_requirements_have_an_explanation(self):
        with patch('agent_manager.runtime.native.codex_continuation.sys.platform', 'darwin'):
            manager = Manager('codex', self.config, self.base / 'state')
        caps = manager.provider_spec.capabilities()
        self.assertFalse(caps['crossEnvironmentResume'])
        self.assertIn('Linux', caps['crossEnvironmentResumeReason'])
        with patch('agent_manager.runtime.native.codex_continuation.sys.platform', 'linux'), \
                patch('agent_manager.runtime.native.codex_continuation.shutil.which', return_value='/fixture/bwrap'):
            manager = Manager('codex', self.config, self.base / 'state')
        self.assertTrue(manager.provider_spec.capabilities()['crossEnvironmentResume'])
        self.assertIsNone(manager.provider_spec.capabilities()['crossEnvironmentResumeReason'])
        manager.new_profile('work'); manager.new_profile('backup')
        self.assertEqual(execution_profiles(manager, {'history_home': str(manager.home('work'))}), ['backup', 'work'])


if __name__ == '__main__':
    unittest.main()
