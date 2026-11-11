import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil
import unittest
from unittest.mock import patch

from agent_manager import Manager, Error
from agent_manager.errors import BusyError
from agent_manager.storage.locks import locks


class EnvironmentLeaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / 'providers.toml'
        self.config.write_text('[providers.codex]\nexecutable=' + json.dumps(sys.executable) + '\n')
        self.manager = Manager('codex', config=self.config, root=self.root / 'agents')
        self.manager.new_profile('work')
        self.identifier = self.manager.resolve_profile('work').environment_id

    def test_codex_launch_and_readers_coexist_with_external_lease(self):
        code = """
import sys, subprocess
from agent_manager import Manager
from agent_manager.runtime.execution import launch_home
manager = Manager('codex', config=sys.argv[1], root=sys.argv[2])
with launch_home(manager, 'work'):
    pass
manager.budget('work', cached=True)
manager.models('work', cached=True)
from unittest.mock import patch
with patch('agent_manager.environments.login.subprocess.run',
           return_value=subprocess.CompletedProcess([], 0, stdout='', stderr='')):
    assert manager.login_status('work')['state'] == 'signed-in'
"""
        with self.manager.lease_native_environment(self.identifier):
            result = subprocess.run([sys.executable, '-c', code, str(self.config),
                                     str(self.root / 'agents')],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            with self.assertRaises(BusyError):
                with locks(self.manager.lock_root, 'environment-' + self.identifier):
                    pass

    def test_login_status_fails_promptly_during_exclusive_maintenance(self):
        code = """
import sys
from unittest.mock import patch
from agent_manager import Manager
from agent_manager.errors import BusyError
manager = Manager('codex', config=sys.argv[1], root=sys.argv[2])
with patch('agent_manager.environments.login.subprocess.run') as probe:
    try:
        manager.login_status('work')
    except BusyError:
        pass
    else:
        raise AssertionError('Status must report busy during exclusive maintenance')
    probe.assert_not_called()
"""
        with locks(self.manager.lock_root, 'environment-' + self.identifier):
            result = subprocess.run([sys.executable, '-c', code, str(self.config),
                                     str(self.root / 'agents')],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_revision_guard_and_ambient_credential_isolation(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'ambient-secret', 'CODEX_HOME': '/wrong'}):
            with self.manager.lease_native_environment(self.identifier) as lease:
                descriptor = lease.descriptor
                self.assertNotIn('OPENAI_API_KEY', lease.env)
                self.assertEqual(lease.env['CODEX_HOME'], str(self.manager.home('work')))
                self.assertNotIn('ambient-secret', str(descriptor))
                with self.assertRaises(BusyError):
                    with locks(self.manager.lock_root, 'environment-' + self.identifier):
                        pass
        with locks(self.manager.lock_root, 'environment-' + self.identifier):
            pass
        with self.assertRaises(Error):
            with self.manager.lease_native_environment(self.identifier, expected_revision='stale'):
                pass
        with self.manager.lease_native_environment(self.identifier, expected_revision=descriptor['revision']):
            pass

    def test_child_can_retain_guard_after_owner_closes_it(self):
        with self.manager.lease_native_environment(self.identifier) as lease:
            with lease.subprocess_options() as options:
                child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.buffer.read()'],
                    stdin=subprocess.PIPE, **options)
        try:
            with self.assertRaises(BusyError):
                with locks(self.manager.lock_root, 'environment-' + self.identifier):
                    pass
        finally:
            child.communicate(timeout=5)
        with locks(self.manager.lock_root, 'environment-' + self.identifier):
            pass

    def test_unknown_id_never_creates_a_native_home(self):
        import uuid
        before = list(self.manager.managed_homes.iterdir())
        with self.assertRaises(Error):
            with self.manager.lease_native_environment(str(uuid.uuid4())):
                pass
        self.assertEqual(list(self.manager.managed_homes.iterdir()), before)

    def test_long_unicode_registry_retains_the_environment_guard(self):
        root = self.root.joinpath(*(['registry with spaces 界' * 2] * 6))
        if os.name == 'nt':
            root = Path('\\\\?\\' + str(root))
            self.addCleanup(shutil.rmtree, '\\\\?\\' + str(self.root))
        manager = Manager('codex', config=self.config, root=root)
        manager.new_profile('work')
        identifier = manager.resolve_profile('work').environment_id
        with manager.lease_native_environment(identifier):
            with self.assertRaises(BusyError):
                with locks(manager.lock_root, 'environment-' + identifier):
                    pass
        with locks(manager.lock_root, 'environment-' + identifier):
            pass

    @unittest.skipIf(os.name == 'nt', 'POSIX directory alias')
    def test_default_registry_alias_matches_a_reloaded_environment(self):
        actual = self.root / 'actual'
        actual.mkdir()
        alias = self.root / 'alias'
        alias.symlink_to(actual, target_is_directory=True)
        with patch.dict(os.environ, {'AGENT_MANAGER_HOME': str(alias)}):
            manager = Manager('codex', config=self.config)
            manager.new_profile('work')
            identifier = manager.resolve_profile('work').environment_id
            with manager.lease_native_environment(identifier) as lease:
                descriptor = lease.descriptor
            reloaded = Manager('codex', config=descriptor['config'], root=descriptor['root'])
            with reloaded.lease_native_environment(identifier, expected_revision=descriptor['revision']) as lease:
                self.assertEqual(lease.descriptor, descriptor)

    def test_opencode_home_isolated_and_exclusive_without_ambient_credentials(self):
        with self.config.open('a') as stream:
            stream.write('\n[providers.opencode]\nexecutable=' + json.dumps(sys.executable) + '\n')
        manager = Manager('opencode', config=self.config, root=self.root / 'agents')
        manager.new_profile('work')
        identifier = manager.resolve_profile('work').environment_id
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'wrong-account', 'OPENCODE_DB': '/wrong/database',
            'OPENCODE_CONFIG_CONTENT': '{"wrong":true}'}):
            with manager.lease_native_environment(identifier) as lease:
                self.assertEqual(lease.descriptor['provider'], 'opencode')
                self.assertTrue(Path(lease.env['OPENCODE_DB']).is_relative_to(manager.home('work')))
                self.assertNotIn('OPENAI_API_KEY', lease.env)
                self.assertNotIn('OPENCODE_CONFIG_CONTENT', lease.env)
                with self.assertRaises(BusyError):
                    with manager.lease_native_environment(identifier):
                        pass
        with manager.lease_native_environment(identifier):
            pass
