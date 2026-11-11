"""Opt-in installed protocol evidence without credentials or inference."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from conversation_manager.native_requests import schemas


@unittest.skipUnless(os.environ.get('CODEX_TEST_BIN'), 'Set CODEX_TEST_BIN to verify installed protocol schemas')
class InstalledSchemaTests(unittest.TestCase):
    def test_packaged_method_schemas_match_installed_codex(self):
        names = {'item/permissions/requestApproval': 'PermissionsRequestApprovalResponse',
                 'item/commandExecution/requestApproval': 'CommandExecutionRequestApprovalResponse',
                 'item/fileChange/requestApproval': 'FileChangeRequestApprovalResponse',
                 'item/tool/requestUserInput': 'ToolRequestUserInputResponse',
                 'execCommandApproval': 'ExecCommandApprovalResponse', 'applyPatchApproval': 'ApplyPatchApprovalResponse'}
        def strip(value):
            if isinstance(value, dict): return {k: strip(v) for k, v in value.items() if k not in ('description', 'title', '$schema')}
            if isinstance(value, list): return [strip(v) for v in value]
            return value
        with tempfile.TemporaryDirectory(prefix='codex-schemas-') as directory:
            result = subprocess.run([os.environ['CODEX_TEST_BIN'], 'app-server', 'generate-json-schema', '--out', directory],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            for method, name in names.items():
                self.assertEqual(schemas()[method], strip(json.loads((Path(directory)/(name+'.json')).read_text())), method)
