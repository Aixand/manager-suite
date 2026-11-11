"""Agent-owned public surfaces and execution boundaries after resource removal."""
from contextlib import redirect_stderr
import importlib.util
import inspect
import io
import unittest
from unittest.mock import patch

from agent_manager import Client, Error, Manager
from agent_manager.interfaces.cli.parser import parser
from agent_manager.interfaces.management_contract import TOOLS
from tests.test_budget_interfaces import BudgetFixture


class StandaloneBoundaryTests(BudgetFixture, unittest.TestCase):
    def test_removed_management_apis_and_launch_selectors_are_not_public(self):
        removed = ('profile_mcp', 'configure_mcp', 'prepare_mcp', 'mcp_jobs',
                   'profile_resources', 'set_profile_resources', 'capture_resources',
                   'profile_skills', 'set_profile_skills', 'preview_skills',
                   'profile_instructions', 'set_profile_instructions', 'preview_instructions',
                   'prepare_application', 'application', 'prompts', 'resource_catalog',
                   'resource_migration', 'export_resource_assignments')
        for owner in (Manager, Client):
            for method in removed: self.assertFalse(hasattr(owner, method), f'{owner}.{method}')
        parameters = inspect.signature(Client.spawn).parameters
        for name in ('resources', 'mcp', 'instructions', 'skills', 'application_resources', 'runtime_bindings'):
            self.assertNotIn(name, parameters)
        self.assertIsNone(importlib.util.find_spec('agent_manager.resources'))
        self.assertTrue(any(row['name'] == 'list_profiles' for row in TOOLS))
        self.assertTrue(any(row['name'] == 'set_native_instructions' for row in TOOLS))

    def test_retired_cli_commands_and_selectors_are_rejected(self):
        for suffix in (['resource-migration', 'inspect'], ['run', 'work', '--carrier'], ['mcp', 'list'], ['application', 'prepare'], ['profile', 'skills', 'show', 'work'],
                       ['profile', 'instructions', 'show', 'work'], ['profile', 'resources', 'show', 'work'],
                       ['run', 'work', '--resources', '{}'], ['run', 'work', '--instructions', 'release']):
            with self.subTest(suffix=suffix), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                parser().parse_args(['codex', *suffix])
            self.assertEqual(caught.exception.code, 2)

    def test_launch_preserves_native_guidance_and_ignores_legacy_assignments(self):
        manager = self.manager
        identifier = manager.resolve_profile('work').environment_id
        state = manager.state()
        for key in ('instruction_bindings', 'mcp_bindings', 'skill_bindings'):
            state[key] = {identifier: {'schema': 'legacy-fixture', 'target': 'unavailable'}}
        manager.save(state)
        native = manager.home('work') / 'AGENTS.md'
        native.write_text('User instructions and a manually changed old section.')
        old_source = manager.root / 'native-instructions' / 'AGENTS.md'
        old_source.parent.mkdir(parents=True)
        old_source.write_text('Old provider-wide guidance')
        before_native, before_source = native.read_bytes(), old_source.read_bytes()
        with patch.object(manager, '_invoke_native', return_value=0) as invoke:
            self.assertEqual(manager._run_native('work', ['--yolo']), 0)
        self.assertEqual(invoke.call_args.args[1], ['--no-daemon', '--yolo'])
        self.assertEqual(native.read_bytes(), before_native)
        self.assertEqual(old_source.read_bytes(), before_source)
        for key in ('instruction_bindings', 'mcp_bindings', 'skill_bindings'):
            self.assertEqual(manager.state()[key], state[key])
        self.assertFalse((manager.root / 'resources').exists())
        self.assertFalse((manager.root / 'instructions').exists())


    def test_removed_resource_and_carrier_code_is_absent(self):
        import ast
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / 'agent_manager'
        for path in ('runtime/legacy', 'runtime/integrations/carrier_binding.py', 'environments/resource_migration.py'):
            self.assertFalse((root / path).exists(), path)
        for path in root.rglob('*.py'):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertFalse((node.module or '').startswith('agent_manager.runtime.legacy'), str(path))

    def test_carrier_preferences_are_rejected(self):
        from agent_manager.providers.config import load_registry
        self.manager.config_path.write_text('[providers.codex.carrier]\nurl="http://localhost:8765"\n')
        with self.assertRaisesRegex(Error, 'Unsupported profile-only preferences.*carrier'):
            load_registry(self.manager.config_path)

    def test_undercurrent_extra_does_not_install_server_manager(self):
        from pathlib import Path
        import tomllib
        project = tomllib.loads((Path(__file__).resolve().parents[1] / 'pyproject.toml').read_text())['project']
        dependencies = project['optional-dependencies']['undercurrent']
        self.assertEqual(len(dependencies), 1)
        self.assertTrue(dependencies[0].startswith('aixand-undercurrent-sdk'))
        self.assertFalse(any('server-manager' in row for row in project['dependencies']))
