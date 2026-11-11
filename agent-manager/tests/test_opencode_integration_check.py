"""Isolation and honest success/failure reporting of the opt-in native check."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import OpenerDirector

from agent_manager.models.schema import ModelError
from tests.manual import opencode_integrations as manual


def native_fixture(manager, home, cwd, policy):
    rows, details, providers = [], [], []
    for key, variable in (('openai', 'OPENAI_API_KEY'), ('openrouter', 'OPENROUTER_API_KEY')):
        active = variable in os.environ
        connection = dict(type='env', name=variable)
        rows.append(dict(id=key, name=key, methods=[dict(type='key'), dict(type='env')],
            connections=[connection] if active else []))
        details.append(dict(id=key,
            active_connection=dict(id='env:' + variable, type='env', method='key') if active else None,
            budget=dict(status='unknown', reason='reader-unavailable' if active else 'no-active-connection')))
        providers.append(dict(id=key, integration_id=key, package='@opencode/ai/providers/' + key,
            endpoint_reviewed=True))
    return dict(data=rows), dict(schema='agent-manager/opencode-bridge-2', version='2.0.18',
        integrations=details, providers=providers, default_model=None), '2.0.18'


class NativeIntegrationCheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='agent-native-check-test-')
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name) / 'results'
        self.arguments = ['--executable', sys.executable, '--output', str(self.output)]
        self.enterContext(redirect_stdout(io.StringIO()))
        self.enterContext(redirect_stderr(io.StringIO()))

    def result(self):
        return json.loads((self.output / 'results.json').read_text())

    def test_prepare_only_never_starts_native_and_disables_every_remote_quota_reader(self):
        with patch.object(manual.opencode, 'read', side_effect=AssertionError('Unexpected native execution')):
            self.assertEqual(manual.main([*self.arguments, '--prepare-only']), 0)
        report = self.result()
        self.assertEqual(report['status'], 'prepared')
        self.assertFalse(report['native_plugin_verified'])
        self.assertFalse(report['authenticated_quota_verified'])
        for path in (self.output / 'services').glob('*.toml'):
            self.assertNotIn('budget_adapter = "openrouter-key"', path.read_text())
            self.assertNotIn('budget_adapter = "openai-native"', path.read_text())

    def test_parent_credentials_configuration_and_server_routes_are_not_forwarded(self):
        observed = []
        def read(manager, home, cwd, policy):
            self.assertTrue(home.is_relative_to(self.output))
            self.assertTrue(cwd.is_relative_to(self.output))
            self.assertTrue(Path(os.environ['HOME']).is_relative_to(self.output))
            for key in ('OPENCODE_SERVER_PASSWORD', 'OPENCODE_DB', 'AGENT_MANAGER_CONFIG', 'ANTHROPIC_API_KEY'):
                self.assertNotIn(key, os.environ)
            self.assertTrue(all(auth.budget_adapter == 'disabled' for rule in policy.rules for auth in rule.authentication))
            self.assertNotIn('parent-secret', json.dumps(dict(os.environ)))
            observed.append((home, 'OPENAI_API_KEY' in os.environ))
            return native_fixture(manager, home, cwd, policy)
        with patch.dict(os.environ, OPENAI_API_KEY='parent-secret', ANTHROPIC_API_KEY='parent-secret',
                OPENCODE_SERVER_PASSWORD='parent-secret', OPENCODE_DB='/not-used', AGENT_MANAGER_CONFIG='/not-used'), \
                patch.object(manual.opencode, 'read', side_effect=read):
            self.assertEqual(manual.main(self.arguments), 0)
            self.assertEqual(os.environ['OPENAI_API_KEY'], 'parent-secret')
        self.assertEqual([active for _, active in observed], [False, True, False, False])
        self.assertEqual(observed[1][0], observed[2][0])
        self.assertNotEqual(observed[2][0], observed[3][0])
        report = self.result()
        self.assertEqual(report['status'], 'passed')
        self.assertTrue(report['native_plugin_verified'])
        self.assertEqual(len(report['cases']), 4)
        self.assertEqual(report['model_calls'], 0)
        self.assertFalse(report['authenticated_quota_verified'])

    def test_missing_plugin_never_counts_as_a_successful_native_check(self):
        def read(*args):
            native, _, version = native_fixture(*args)
            return native, None, version
        with patch.object(manual.opencode, 'read', side_effect=read):
            self.assertEqual(manual.main(self.arguments), 1)
        self.assertEqual(self.result()['status'], 'failed')
        self.assertFalse(self.result()['native_plugin_verified'])

    def test_missing_synthetic_connection_fails_instead_of_qualifying_empty_metadata(self):
        def read(*args):
            native, bridge, version = native_fixture(*args)
            if 'OPENAI_API_KEY' in os.environ:
                native['data'][0]['connections'] = []
                bridge['integrations'][0]['active_connection'] = None
            return native, bridge, version
        with patch.object(manual.opencode, 'read', side_effect=read):
            self.assertEqual(manual.main(self.arguments), 1)
        self.assertEqual(len(self.result()['cases']), 1)
        self.assertFalse(self.result()['native_plugin_verified'])

    def test_socket_denial_is_reported_as_blocked_with_a_failing_exit_code(self):
        def read(*args):
            (self.output / 'empty.log').write_text('EPERM: operation not permitted, listen')
            raise ModelError('invalid-response')
        with patch.object(manual.opencode, 'read', side_effect=read):
            self.assertEqual(manual.main(self.arguments), 2)
        self.assertEqual(self.result()['status'], 'blocked')
        self.assertEqual(self.result()['error_code'], 'invalid-response')
        self.assertFalse(self.result()['native_plugin_verified'])

    def test_synthetic_secret_in_native_metadata_is_not_published_as_a_success(self):
        def read(*args):
            native, bridge, version = native_fixture(*args)
            native['data'][0]['name'] = manual.SYNTHETIC['OPENAI_API_KEY']
            return native, bridge, version
        with patch.object(manual.opencode, 'read', side_effect=read):
            self.assertEqual(manual.main(self.arguments), 1)
        self.assertFalse(self.result()['native_plugin_verified'])
        self.assertNotIn(manual.SYNTHETIC['OPENAI_API_KEY'], json.dumps(self.result()))

    def test_existing_output_directory_is_not_reused(self):
        self.output.mkdir(); marker = self.output / 'keep'; marker.write_text('unchanged')
        with self.assertRaises(FileExistsError): manual.main(self.arguments)
        self.assertEqual(marker.read_text(), 'unchanged')

    def test_manual_http_diagnostics_preserve_the_error_body_for_classification(self):
        self.output.mkdir()
        raw = b'{"_tag":"RpcError","type":"rpc.invalid_input","message":"Synthetic schema failure"}'
        with patch.object(OpenerDirector, 'open', side_effect=HTTPError('http://127.0.0.1/rpc',
                400, 'Bad request', {}, io.BytesIO(raw))):
            with manual.diagnostics(self.output / 'empty.log'):
                with self.assertRaises(HTTPError) as failure:
                    OpenerDirector().open('http://127.0.0.1/rpc')
                self.assertEqual(failure.exception.read(), raw)
                failure.exception.close()
        row = json.loads((self.output / 'empty.http-errors.jsonl').read_text())
        self.assertEqual(row['status'], 400)
        self.assertEqual(row['response']['type'], 'rpc.invalid_input')
