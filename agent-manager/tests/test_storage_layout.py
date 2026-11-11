"""Agent Manager data and native provider homes have distinct stable roots."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_manager._paths import data_root, managed_homes_root, state_root
from agent_manager.providers.paths import configuration_path


class StorageLayoutTests(unittest.TestCase):
    def test_default_layout_is_contained_by_aixand_agent_manager(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            with patch.dict(os.environ, {}, clear=True), patch.object(
                Path, "home", return_value=home
            ):
                base = home / ".local/share/aixand/manager/agent"
                self.assertEqual(data_root(), base)
                self.assertEqual(configuration_path(), base / "providers.toml")
                self.assertEqual(state_root(), base / "state")
                self.assertEqual(managed_homes_root(), base / "home")

    def test_custom_state_root_keeps_managed_homes_self_contained(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "isolated"
            self.assertEqual(state_root(root), root)
            self.assertEqual(managed_homes_root(root), root / "home")

    def test_explicit_managed_homes_preserve_existing_accounts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.dict(os.environ, {'AGENT_MANAGER_HOMES_ROOT': str(root / 'old-home')}):
                self.assertEqual(managed_homes_root(root / 'state'), root / 'old-home')


if __name__ == "__main__":
    unittest.main()
