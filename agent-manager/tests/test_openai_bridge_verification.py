"""Focused checks for the opt-in OAuth handoff qualification tool."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tests.manual import opencode_openai_bridge as manual


class OpenAIBridgeVerificationTests(unittest.TestCase):
    def test_child_environment_drops_ambient_credentials_and_native_configuration(self):
        with patch.dict(os.environ, OPENAI_API_KEY='parent-secret', CODEX_HOME='/not-used',
                OPENCODE_CONFIG_CONTENT='parent-secret', OPENCODE_DB='/not-used', AGENT_MANAGER_CONFIG='/not-used'):
            variables = manual.environment(Path('/tmp/disposable-example'))
        for key in ('OPENAI_API_KEY', 'CODEX_HOME', 'OPENCODE_DB', 'AGENT_MANAGER_CONFIG'):
            self.assertNotIn(key, variables)
        self.assertEqual(variables['OPENCODE_CONFIG_CONTENT'], '{}')
        self.assertNotIn('parent-secret', json.dumps(variables))

    @unittest.skipIf(os.name == 'nt' or shutil.which('node') is None, 'POSIX executable fixture and Node required')
    def test_native_token_handoff_transport_and_plugin_boundaries(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='agent-openai-bridge-test-') as temporary:
            fixture = Path(temporary) / 'codex-fixture'
            shutil.copyfile(root / 'tests/fixtures/codex_external_probe.py', fixture)
            fixture.chmod(0o700)
            result = subprocess.run([shutil.which('node'), str(root / 'tests/fixtures/openai_bridge_verification.mjs'),
                str(fixture)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__': unittest.main()
