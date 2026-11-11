"""Package removal verifies ownership before constructing package-manager argv."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_manager.providers.installation import software


@unittest.skipIf(os.name == 'nt', 'POSIX package layouts')
class PackageUninstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.prefix = Path(temporary.name)
        self.manager = SimpleNamespace(provider='copilot')
        self.package = self.prefix / 'lib/node_modules/@github/copilot'
        self.package.mkdir(parents=True)
        (self.package / 'package.json').write_text(json.dumps({'name': '@github/copilot', 'bin': {'copilot': 'loader.js'}}))
        (self.package / 'loader.js').write_text('fixture')
        self.path = self.prefix / 'bin/copilot'
        self.path.parent.mkdir()
        self.path.symlink_to(self.package / 'loader.js')
        self.enterContext(patch('agent_manager.providers.installation.engine.manager_prefix', return_value=self.prefix))
        self.enterContext(patch('agent_manager.providers.installation.engine.package_manager_command', side_effect=lambda name, args: [name, *args]))

    def test_npm_removal_targets_only_the_verified_prefix_and_package(self):
        self.assertEqual(software.package_removal(self.manager, self.path, 'npm'),
            ['npm', 'uninstall', '--global', '--prefix', str(self.prefix), '@github/copilot'])

    def test_wrong_prefix_package_or_entry_is_not_removable(self):
        with patch('agent_manager.providers.installation.engine.manager_prefix', return_value=self.prefix / 'other'):
            self.assertEqual(software.package_removal(self.manager, self.path, 'npm'), [])
        (self.package / 'package.json').write_text(json.dumps({'name': 'other', 'bin': 'loader.js'}))
        self.assertEqual(software.package_removal(self.manager, self.path, 'npm'), [])

    def test_npm_symlink_must_match_registered_entry(self):
        other = self.package / 'other.js'; other.write_text('other')
        self.path.unlink(); self.path.symlink_to(other)
        self.assertEqual(software.package_removal(self.manager, self.path, 'npm'), [])

    def test_malformed_package_metadata_is_not_removable(self):
        (self.package / 'package.json').write_text('[]')
        self.assertEqual(software.package_removal(self.manager, self.path, 'npm'), [])

    def test_homebrew_requires_matching_package_location(self):
        target = self.prefix / 'Caskroom/copilot-cli/1.0/bin/copilot'
        target.parent.mkdir(parents=True); target.write_text('fixture')
        self.path.unlink(); self.path.symlink_to(target)
        self.assertEqual(software.package_removal(self.manager, self.path, 'brew'),
            ['brew', 'uninstall', '--cask', 'copilot-cli'])
        self.assertEqual(software.package_removal(SimpleNamespace(provider='claude'), self.path, 'brew'), [])
