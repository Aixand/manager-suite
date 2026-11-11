import unittest
import json
import os
import subprocess
import sys
from unittest.mock import patch

from agent_manager.providers.installation import engine as install


class SetupEnvironmentTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'POSIX descriptor blocking flags')
    def test_metadata_probes_cannot_change_the_callers_protocol_input(self):
        probe = "import os; os.set_blocking(0, False); print('example 1.2.3')"
        runner = f"""import json, os, sys
from agent_manager.providers.installation.engine import output
version = output([sys.executable, '-c', {probe!r}])
print(json.dumps([version, os.get_blocking(0)]))
"""
        result = subprocess.run([sys.executable, '-c', runner], input='', capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), ['example 1.2.3', True])

    def test_detects_execution_host_and_wsl(self):
        with patch.object(install.platform, 'system', return_value='Linux'), \
                patch.object(install.platform, 'machine', return_value='aarch64'), \
                patch.object(install.platform, 'release', return_value='6.6-microsoft-standard-WSL2'), \
                patch.object(install.shutil, 'which', side_effect=lambda tool: '/tools/' + tool if tool in ('bash', 'curl') else None):
            environment = install.environment_info()
        self.assertEqual((environment['os'], environment['label'], environment['architecture']), ('linux', 'Linux (WSL)', 'arm64'))
        self.assertTrue(environment['wsl'])
        self.assertTrue(environment['managedSupported'])
        self.assertEqual(environment['tools'], ['curl', 'bash'])

    def test_windows_guidance_uses_native_managed_installation(self):
        with patch.object(install.platform, 'system', return_value='Windows'), \
                patch.object(install.platform, 'machine', return_value='AMD64'), \
                patch.object(install.shutil, 'which', return_value=None):
            environment = install.environment_info()
            opencode = install.setup_guide('opencode')
            claude = install.setup_guide('claude')
        self.assertTrue(environment['managedSupported'])
        self.assertEqual([row['id'] for row in opencode['methods']], ['installer'])
        self.assertIn('agent install opencode --installer --version', opencode['methods'][0]['command'])
        self.assertIn('agent install claude --installer --version', claude['methods'][0]['command'])
        self.assertNotIn('bash', claude['methods'][0]['command'])

    def test_guides_share_install_recipes_and_report_missing_tools(self):
        with patch.object(install.platform, 'system', return_value='Darwin'), \
                patch.object(install.platform, 'machine', return_value='arm64'), \
                patch.object(install.shutil, 'which', return_value=None):
            guide = install.setup_guide('opencode')
        self.assertEqual(guide['verifyCommand'], 'opencode --version')
        self.assertIn(install.catalog()['opencode']['installer_url'], guide['methods'][0]['command'])
        self.assertIn('Missing:', guide['methods'][0]['error'])
        self.assertEqual(len(guide['methods']), 1)
        self.assertIn(install.catalog()['opencode']['recommended_version'], guide['methods'][0]['command'])

    def test_unknown_os_has_no_installation_methods_and_antigravity_uses_verified_artifacts(self):
        with patch.object(install.platform, 'system', return_value='OtherOS'):
            self.assertFalse(install.environment_info()['managedSupported'])
            self.assertEqual(install.setup_guide('claude')['methods'], [])
            self.assertEqual(install.setup_guide('antigravity')['verifyCommand'], 'agy --version')
            self.assertIsNone(install.setup_guide('other'))
        self.assertIn('claude', install.catalog())
        self.assertTrue(install.catalog()['antigravity']['pinned_artifact'])
