"""All independently installed managers follow the same default-path contract."""
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
for product in ('agent', 'conversation', 'mcp', 'prompt', 'skill'):
    package = ROOT / (product + '-manager')
    sys.path.insert(0, str(package if product == 'agent' else package / 'src'))


class ManagerPathsTests(unittest.TestCase):
    def test_default_policy_and_suite_override(self):
        for product in ('agent', 'conversation', 'mcp', 'prompt', 'skill'):
            module = importlib.import_module(product + '_manager.' + ('_default_paths' if product == 'agent' else '_paths'))
            with self.subTest(product=product), tempfile.TemporaryDirectory() as folder:
                home = Path(folder)
                with patch.dict(os.environ, {}, clear=True), patch.object(Path, 'home', return_value=home):
                    expected = home / '.local/share/aixand/manager' / product
                    self.assertEqual(module.default_root(), expected)
                    for value in ('', 'relative/data'):
                        with patch.dict(os.environ, XDG_DATA_HOME=value):
                            self.assertEqual(module.default_root(), expected)
                    with patch.dict(os.environ, XDG_DATA_HOME=str(home / 'xdg')):
                        self.assertEqual(module.default_root(), home / 'xdg/aixand/manager' / product)
                        with patch.dict(os.environ, AIXAND_MANAGER_ROOT=str(home / 'suite')):
                            self.assertEqual(module.default_root(), home / 'suite' / product)
                    self.assertEqual(list(home.iterdir()), [])

    def test_public_home_overrides_and_config_locations(self):
        from agent_manager._paths import state_root, managed_homes_root
        from agent_manager.providers.paths import configuration_path
        from conversation_manager.config import path, state_home
        from mcp_manager import Manager as Mcp
        from prompt_manager import Manager as Prompt
        from skill_manager import Manager as Skill
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.dict(os.environ, {'AIXAND_MANAGER_ROOT': str(root / 'suite')}, clear=True):
                self.assertEqual(configuration_path(), root / 'suite/agent/providers.toml')
                self.assertEqual(state_root(), root / 'suite/agent/state')
                self.assertEqual(managed_homes_root(), root / 'suite/agent/home')
                self.assertEqual(path(), root / 'suite/conversation/config.toml')
                self.assertEqual(state_home(), root / 'suite/conversation/state')
                for product, manager in [('mcp', Mcp), ('prompt', lambda *args: Prompt(*args).store), ('skill', Skill)]:
                    with self.subTest(product=product):
                        self.assertEqual(manager().home, root / 'suite' / product)
                        with patch.dict(os.environ, {product.upper() + '_MANAGER_HOME': str(root / 'env')}):
                            self.assertEqual(manager().home, root / 'env')
                            self.assertEqual(manager(root / 'explicit').home, root / 'explicit')
                with patch.dict(os.environ, AGENT_MANAGER_HOME=str(root / 'state'), CONVERSATION_MANAGER_HOME=str(root / 'conversation')):
                    self.assertEqual(state_root(), root / 'state')
                    self.assertEqual(state_root(root / 'explicit'), root / 'explicit')
                    self.assertEqual(state_home(), root / 'conversation')
                    self.assertEqual(state_home(root / 'explicit'), root / 'explicit')
                    self.assertEqual(Mcp().home, root / 'suite/mcp')
                with patch.dict(os.environ, AGENT_MANAGER_CONFIG=str(root / 'agent.toml'), CONVERSATION_MANAGER_CONFIG=str(root / 'conversation.toml')):
                    self.assertEqual(configuration_path(), root / 'agent.toml')
                    self.assertEqual(configuration_path(root / 'explicit.toml'), root / 'explicit.toml')
                    self.assertEqual(path(), root / 'conversation.toml')
                    self.assertEqual(path(root / 'explicit.toml'), root / 'explicit.toml')
                self.assertEqual(list(root.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
