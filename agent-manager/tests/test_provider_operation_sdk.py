"""Public maintenance capture/dispatch without downloads, accounts or daemons."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_manager import Error, Manager


class ProviderOperationSdkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='provider-operation-sdk-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        config = self.root / 'providers.toml'
        config.write_text('[providers.codex]\nexecutable="unused-codex"\n')
        self.manager = Manager('codex', config=config, root=self.root / 'agents')

    def test_installation_capture_does_not_execute_or_mutate_the_request(self):
        request = {'kind': 'install', 'method': 'installer'}
        original = copy.deepcopy(request)
        plan = {'provider': 'codex', 'kind': 'install', 'version': '0.166.0'}
        with patch('agent_manager.providers.installation.plans.resolve', return_value=plan), \
             patch('agent_manager.providers.installation.plans.execute') as execute:
            captured = self.manager.prepare_provider_operation(request)
        self.assertEqual(request, original)
        self.assertEqual(captured['installationPlan'], plan)
        self.assertEqual(captured['home'], str(self.manager.root.parent.resolve()))
        execute.assert_not_called()

    def test_worker_executes_the_captured_plan_and_relays_manager_progress(self):
        plan = {'provider': 'codex', 'kind': 'install', 'version': '0.166.0'}
        progress = []
        def install(manager, captured, *, progress):
            self.assertEqual(captured, plan)
            progress({'phase': 'installing'})
            return 0
        with patch('agent_manager.providers.installation.plans.resolve', return_value=plan), \
             patch('agent_manager.providers.installation.plans.validate', return_value=plan), \
             patch('agent_manager.providers.installation.plans.execute', side_effect=install), \
             patch('agent_manager.providers.catalog.active_profiles', return_value=[]):
            captured = self.manager.prepare_provider_operation({'kind': 'install'})
            result = self.manager.perform_provider_operation(captured, progress=progress.append)
        self.assertEqual(result['installationPlan'], plan)
        self.assertEqual(progress, [{'phase': 'installing'}])

    def test_login_captures_immutable_identity_and_rejects_an_alias_replacement(self):
        self.manager.new_profile('work')
        captured = self.manager.prepare_provider_operation({'kind': 'profile-login', 'profile': 'work'})
        self.assertTrue(captured['environment_id'])
        self.assertTrue(captured['profile_id'])
        self.manager.rename('work', 'renamed')
        self.manager.new_profile('work')
        with patch.object(Manager, 'login', return_value=0) as login:
            result = self.manager.perform_provider_operation(captured)
        self.assertEqual(result['environment_id'], captured['environment_id'])
        self.assertNotEqual(result['environment_id'], self.manager.resolve_profile('work').environment_id)
        self.assertEqual(str(login.call_args.args[0]), 'renamed')

    def test_stale_environment_revision_is_rejected_before_native_login(self):
        self.manager.new_profile('work')
        captured = self.manager.prepare_provider_operation({'kind': 'profile-login', 'profile': 'work'})
        self.manager.set_launch_arguments(['--no-alt-screen'], profile='work')
        with patch.object(Manager, 'login') as login:
            with self.assertRaises(Error):
                self.manager.perform_provider_operation(captured)
        login.assert_not_called()

    def test_worker_rejects_uncaptured_or_foreign_operations(self):
        for request in ({'kind': 'install'}, {'kind': 'profile-login', 'profile': 'work'},
                        {'kind': 'install', 'provider': 'opencode', 'installationPlan': {}},
                        {'kind': 'install', 'home': str(self.root / 'other'), 'installationPlan': {}}):
            with self.subTest(request=request), self.assertRaises(Error):
                self.manager.perform_provider_operation(request)


if __name__ == '__main__':
    unittest.main()
