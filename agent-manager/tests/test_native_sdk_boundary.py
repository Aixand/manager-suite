"""Agent Manager's SDK owns native execution without conversation backends."""
import ast
import inspect
from pathlib import Path
import tomllib
import unittest
from unittest.mock import patch

import agent_manager
from agent_manager import Client, Manager
from agent_manager.interfaces.cli.parser import parser
from tests.support import ManagedFixture

ROOT = Path(__file__).resolve().parents[1]


class NativeSDKBoundaryTests(unittest.TestCase):
    def test_no_server_or_provider_sdk_dependency_in_any_extra(self):
        project = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']
        dependencies = project['dependencies'] + [item for group in project['optional-dependencies'].values() for item in group]
        for item in dependencies:
            self.assertFalse(any(name in item for name in ('server-manager', 'claude-agent-sdk', 'github-copilot-sdk', 'google-antigravity')))
        self.assertEqual(project['optional-dependencies']['sdk'], project['optional-dependencies']['undercurrent'])
        self.assertNotIn('backends', project['optional-dependencies'])
        policy = tomllib.loads((ROOT / 'agent_manager/providers/provider-policy.toml').read_text())
        self.assertNotIn('backends', policy)
        self.assertTrue(all('sdk' not in row for row in policy['providers'].values()))
        self.assertFalse(list((ROOT / 'agent_manager/providers').glob('provider-sdk-*.txt')))

    def test_removed_conversation_surfaces_are_not_exported(self):
        for name in ('run', 'invoke'):
            self.assertFalse(hasattr(Manager, name), name)
        for name in ('AppServer', 'AppServerError', 'AppServerFinishedError', 'ManagedConnection', 'UndercurrentLauncher'):
            self.assertFalse(hasattr(agent_manager, name), name)
        for name in ('app_server', 'launch_backend', 'connect_backend', 'backend_catalog', 'discover_backend',
                     'resume_backend_session', 'backend_history', 'backend_turn_result', 'backend_operation',
                     'backend_launch', 'backend_launch_for_operation', 'prepared_backend', 'catalog_snapshot',
                     'remember_catalog', 'install_provider_sdk'):
            self.assertFalse(hasattr(Client, name), name)
        for name in ('mode', 'app_server_run_id'):
            self.assertNotIn(name, inspect.signature(Client.spawn).parameters)
        self.assertNotIn('app_server_group', inspect.signature(Client).parameters)
        choices = next(action for action in parser()._actions if hasattr(action, 'choices') and isinstance(action.choices, dict)).choices
        self.assertNotIn('backend', choices)
        self.assertNotIn('provider-sdk', choices)

    def test_production_code_does_not_import_server_manager(self):
        for path in (ROOT / 'agent_manager').rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                imports = [entry.name for entry in node.names] if isinstance(node, ast.Import) else [node.module or ''] if isinstance(node, ast.ImportFrom) else []
                self.assertFalse(any(name.split('.')[0] == 'server_manager' for name in imports), str(path))
        for name in ('runtime/backends', 'runtime/app_servers', 'providers/installation/sdks.py'):
            self.assertFalse((ROOT / 'agent_manager' / name).exists(), name)


class NativeSDKLaunchTests(ManagedFixture, unittest.IsolatedAsyncioTestCase):
    async def test_launch_and_keyed_recovery_without_conversation_packages(self):
        import uuid
        self.create()
        client = self.client()
        key = str(uuid.uuid4())
        with patch.dict('sys.modules', {'server_manager': None, 'claude_agent_sdk': None, 'copilot': None}):
            first = await self.launch(client, idempotency_key=key)
            second = await self.launch(client, idempotency_key=key)
            self.assertEqual(first, second)
            self.assertEqual((await client.get(first.run_id)).session_id, first.session_id)
            self.assertEqual((await client.runs())[0]['run_id'], first.run_id)

    async def test_cli_direct_setting_cannot_bypass_sdk_runtime(self):
        from agent_manager.runtime.runs import read_run
        self.create()
        client = self.client()
        self.assertEqual(client.manager.config['runtime'], 'direct')
        with patch.object(client.manager, '_run_native', side_effect=AssertionError('CLI execution bypass')), \
                patch.object(client.manager, '_invoke_native', side_effect=AssertionError('Subprocess execution bypass')):
            agent = await self.launch(client)
        row = read_run(client.manager, agent.run_id)
        self.assertEqual(row['runtime'], 'undercurrent')
        self.assertEqual(row['session_id'], agent.session_id)
        for name in ('run', 'invoke'):
            self.assertFalse(hasattr(client.manager, name), name)


class StandaloneAdministrationTests(unittest.TestCase):
    def test_suite_broker_mode_is_not_available(self):
        from contextlib import redirect_stderr
        import io
        from agent_manager.interfaces import management_mcp
        self.assertFalse(hasattr(management_mcp, 'SuiteBackend'))
        with patch('sys.argv', ['agent-manager-mcp', '--suite-socket', '/obsolete']), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                management_mcp.main()
        self.assertEqual(error.exception.code, 2)

    def test_release_policy_has_only_native_cli_operations(self):
        from agent_manager.providers import policy
        self.assertFalse(hasattr(policy, 'sdk_packages'))
        self.assertFalse(hasattr(policy, 'sdk_backend'))
        self.assertNotIn('backend', inspect.signature(policy.compatibility).parameters)
        self.assertNotIn('kind', inspect.signature(policy.component).parameters)
