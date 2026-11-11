"""Antigravity's shared native profile; no keyring or model requests required."""
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
import uuid
from unittest.mock import Mock, patch

from agent_manager import Client, Error, Manager
from agent_manager.interfaces.cli.continuation import continue_session as choose_session
from agent_manager.profiles.operations import delete_profile, duplicate_profile, inspect_profile
from agent_manager.profiles.sessions import continue_session, execution_profiles, remember
from agent_manager.providers.catalog import inspect_installation, profile_readiness
from agent_manager.runtime.execution import launch_home


class AntigravityProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.user = self.base / 'user'; self.user.mkdir()
        self.enterContext(patch.dict(os.environ, HOME=str(self.user), USERPROFILE=str(self.user)))
        self.cli = self.base / 'agy'
        self.cli.write_text('fixture executable; never run'); self.cli.chmod(0o700)
        self.config = self.base / 'providers.toml'
        self.config.write_text('[providers.agy]\nexecutable=' + json.dumps(str(self.cli)) + '\n')
        self.manager = Manager('agy', config=self.config, root=self.base / 'state')
        self.profile = self.manager.new_profile('work')
        self.home = self.user / '.gemini/antigravity-cli'

    def test_one_connection_reports_shared_scopes_and_explicit_resume_limits(self):
        self.assertEqual(self.manager.provider, 'antigravity')
        self.assertEqual(self.manager.home('work'), self.home)
        self.assertFalse(self.profile['managed'])
        self.assertEqual(self.profile['scopes']['homeBinding'], 'shared')
        caps = self.profile['capabilities']
        self.assertFalse(caps['isolatedHomes'])
        self.assertFalse(caps['crossEnvironmentResume'])
        self.assertIn('shared native home and keyring', caps['crossEnvironmentResumeReason'])
        self.assertFalse(caps['retainedSessionResume'])
        self.assertTrue(caps['nativeResumeMayCreateSession'])
        another = self.manager.new_profile('another')
        self.assertEqual(another['environment_id'], self.profile['environment_id'])
        delete_profile(self.manager, 'another')
        with self.assertRaisesRegex(Error, 'cannot be duplicated'):
            duplicate_profile(self.manager, 'work', 'another')
        other = self.base / 'other'; other.mkdir()
        with self.assertRaisesRegex(Error, 'shared native home'):
            self.manager.connect_profile('another', other)

    def test_rename_disconnect_and_reconnect_preserve_native_files(self):
        marker = self.home / 'provider-owned-placeholder'; marker.write_text('native-private-placeholder')
        self.manager.set_default('work')
        self.manager.rename('work', 'renamed')
        renamed = inspect_profile(self.manager, 'renamed')
        self.assertEqual((renamed['profile_id'], renamed['home']), (self.profile['profile_id'], str(self.home)))
        self.assertEqual(self.manager.resolve_profile(), 'renamed')
        result = delete_profile(self.manager, 'renamed')
        self.assertTrue(result['native_data_preserved'])
        self.assertEqual(marker.read_text(), 'native-private-placeholder')
        connected = self.manager.connect_profile('connected', self.home)
        self.assertEqual(connected['home'], str(self.home))
        self.assertNotIn('native-private-placeholder', self.manager.state_path.read_text())

    def test_login_delegates_interactively_without_submitting_usage_or_prompt(self):
        with patch.object(self.manager, '_invoke_native', return_value=0) as native:
            self.assertEqual(self.manager.login('work'), 0)
        self.assertEqual(native.call_args.args, (self.home, []))
        self.assertEqual(inspect_profile(self.manager, 'work')['login']['state'], 'unknown')
        with patch('agent_manager.environments.login.subprocess.run') as native:
            self.assertEqual(self.manager.login_status('work'), {'state': 'unknown', 'verification': 'unsupported'})
        native.assert_not_called()
        with self.assertRaisesRegex(Error, 'interactive logout'):
            self.manager.logout('work')

    def test_login_refuses_an_active_managed_profile(self):
        with patch.object(self.manager, 'active_runs', return_value=[{'pid': os.getpid()}]), \
                patch.object(self.manager, '_invoke_native') as native:
            with self.assertRaisesRegex(Error, 'Stop this profile'):
                self.manager.login('work')
        native.assert_not_called()

    def test_launch_keeps_the_real_shared_home_and_native_keyring_context(self):
        competing = {name: 'private-placeholder' for name in ('GEMINI_API_KEY', 'GOOGLE_API_KEY',
            'GOOGLE_APPLICATION_CREDENTIALS', 'GOOGLE_CLOUD_ACCESS_TOKEN', 'AGY_ADC_AUTH',
            'GOOGLE_GEMINI_BASE_URL', 'GOOGLE_GENAI_USE_VERTEXAI', 'GOOGLE_CLOUD_PROJECT',
            'GOOGLE_CLOUD_LOCATION', 'GOOGLE_CLOUD_QUOTA_PROJECT')}
        env = self.manager.launch_environment(self.home, base=competing | {'HOME': '/wrong/user',
            'DBUS_SESSION_BUS_ADDRESS': 'native-session-bus', 'SSH_CONNECTION': 'native-ssh-context'})
        self.assertEqual(env['HOME'], str(self.user))
        for key in competing: self.assertNotIn(key, env)
        self.assertEqual(env['DBUS_SESSION_BUS_ADDRESS'], 'native-session-bus')
        self.assertEqual(env['SSH_CONNECTION'], 'native-ssh-context')
        with patch('agent_manager.providers.strategies.homes.os', SimpleNamespace(name='nt')):
            env = self.manager.launch_environment(self.home, base={'USERPROFILE': '/wrong/user'})
            self.assertEqual(env['USERPROFILE'], str(self.user))
        with self.assertRaisesRegex(Error, 'shared native home'):
            self.manager.launch_environment(self.base / 'other', base={})

    def test_preferences_and_local_definitions_cannot_fake_an_isolated_home(self):
        self.config.write_text('[providers.agy]\nnative_home="~/another-home"\n')
        with self.assertRaisesRegex(Error, 'does not support redirecting'):
            Manager('agy', self.config, self.base / 'state')
        self.config.write_text('')
        definitions = self.base / 'providers'; definitions.mkdir()
        bundled = Path(__file__).parents[1] / 'agent_manager/providers/definitions/antigravity.toml'
        (definitions / 'antigravity.toml').write_text(bundled.read_text().replace(
            '~/.gemini/antigravity-cli', '~/another-home'))
        with self.assertRaisesRegex(Error, 'shared native home cannot be redirected'):
            Manager('agy', self.config, self.base / 'state')
        (definitions / 'antigravity.toml').write_text(bundled.read_text().replace(
            'strategy = "native-latest"', 'strategy = "native-picker"'))
        with self.assertRaisesRegex(Error, 'exact retained-session continuation is not qualified'):
            Manager('agy', self.config, self.base / 'state')

    def test_cli_continue_never_dispatches_the_native_fresh_session_fallback(self):
        cache = self.home / 'cache'; cache.mkdir()
        cached = cache / 'last_conversations.json'
        cached.write_text(json.dumps({str(self.base): str(uuid.uuid4())}))
        before = cached.read_bytes()
        picker = Mock()
        with patch.object(self.manager, '_run_native') as native, \
                patch('agent_manager.interfaces.cli.continuation.Path.cwd', return_value=self.base):
            with self.assertRaisesRegex(Error, 'can start a new conversation'):
                choose_session(self.manager, picker=picker)
        native.assert_not_called(); picker.assert_not_called()
        self.assertEqual(cached.read_bytes(), before)

    def test_retained_continue_is_rejected_in_cli_sdk_and_direct_preparation(self):
        identifier = str(uuid.uuid4())
        saved = remember(self.manager, 'work', identifier, cwd=self.base)
        self.assertEqual(execution_profiles(self.manager, saved), [])
        client = object.__new__(Client); client.manager = self.manager
        async def inline(function, *args, **kwargs): return function(*args, **kwargs)
        with patch.object(self.manager, '_run_native') as native, \
                patch('agent_manager.interfaces.sdk.asyncio.to_thread', side_effect=inline):
            with self.assertRaisesRegex(Error, 'Exact retained-session continuation is unavailable'):
                continue_session(self.manager, saved['ref'])
            picker = Mock()
            with patch('agent_manager.interfaces.cli.continuation.Path.cwd', return_value=self.base):
                with self.assertRaisesRegex(Error, 'Exact retained-session continuation is unavailable'):
                    choose_session(self.manager, picker=picker)
            picker.assert_not_called()
            with self.assertRaisesRegex(Error, 'Exact retained-session continuation is unavailable'):
                asyncio.run(client.continue_session(saved['ref']))
            with self.assertRaisesRegex(Error, 'Exact retained-session continuation is unavailable'):
                asyncio.run(client.spawn(profile='work', cwd=self.base, resume_id=identifier))
            with self.assertRaisesRegex(Error, 'Exact retained-session continuation is unavailable'):
                with launch_home(self.manager, 'work', cwd=self.base, thread_id=identifier, source_session=saved):
                    self.fail('Unqualified retained resume was prepared')
        native.assert_not_called()


    def test_inventory_does_not_make_a_missing_cli_usable(self):
        with patch('agent_manager.providers.catalog.install.catalog', return_value={}), \
                patch('agent_manager.providers.catalog.install.inspect_external', return_value=None), \
                patch('agent_manager.providers.catalog.install.setup_guide', return_value=None), \
                patch('agent_manager.providers.installation.software.plan', return_value={'disabled': False, 'bundled': False}), \
                patch('agent_manager.providers.installation.exports.status', return_value={}):
            installation = inspect_installation(self.manager)
        self.assertFalse(installation['installed'])
        self.assertFalse(installation['terminalUsable'])
        self.assertFalse(installation['usable'])
