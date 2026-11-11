"""Integration identity, public native metadata, budgets and cache boundaries."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stdout
from dataclasses import replace
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from agent_manager import Client, Error, Manager
from agent_manager.integrations import opencode, schema, service
from agent_manager.models.schema import ModelError, model
from agent_manager.providers.config import load_registry
from agent_manager.profiles.operations import Management, delete_profile
from agent_manager.interfaces.cli.dispatch import execute
from agent_manager.interfaces.management_contract import validate
from agent_manager.interfaces.management_mcp import LocalBackend
from tests.test_budget_interfaces import BudgetFixture


def native_fixture():
    return {'data': [
        dict(id='openai', name='OpenAI', methods=[dict(type='oauth', id='chatgpt'), dict(type='key')],
            connections=[dict(type='credential', id='cred_openai', method='oauth')], metadata={'key': 'fixture-secret'}),
        dict(id='openrouter', name='OpenRouter', methods=[dict(type='key', form={'secret': 'fixture-secret'})],
            connections=[dict(type='credential', id='cred_router', method='key')]),
        dict(id='unreviewed', name='Unreviewed', methods=[dict(type='command', id='cli', command=['fixture-secret'])], connections=[])]}


def bridge_fixture():
    return dict(schema='agent-manager/opencode-bridge-2', version='2.0.18',
        default_model='openrouter/openai/native', providers=[
            dict(id=key, integration_id=key, package='@opencode/ai/providers/' + key, endpoint_reviewed=True)
            for key in ('openai', 'openrouter')], integrations=[
        dict(id='openai', active_connection=dict(type='credential', id='cred_openai', method='oauth'),
            budget=dict(status='unknown', reason='reader-unavailable')),
        dict(id='openrouter', active_connection=dict(type='credential', id='cred_router', method='key'),
            budget=dict(status='partial', reason='account-balance-unavailable', observation=dict(
                limit=100, remaining=75, used=25, reset_interval='monthly', budgets=[
                    dict(id='shared-workspace', scope='workspace', limit=50, remaining=10, used=40, resets_at='2100-01-01T00:00:00Z')])))] )


class IntegrationFixture(BudgetFixture):
    def setUp(self):
        super().setUp()
        self.manager = self.managers['opencode']
        policy = load_registry(self.config).resolve('opencode').integrations
        self.manager.provider_spec = replace(self.manager.provider_spec, integrations=policy)
        self.native, self.bridge = native_fixture(), bridge_fixture()
        self.models = [model('openai/native', provider_id='openai'),
            model('openrouter/openai/native', provider_id='openrouter'),
            model('openrouter/anthropic/other', provider_id='openrouter')]
        self.model_probe = self.enterContext(patch('agent_manager.models.adapters.read',
            return_value=(self.models, 'opencode-models', '2.0.18')))
        def read(*args): return copy.deepcopy(self.native), copy.deepcopy(self.bridge), '2.0.18'
        self.probe = self.enterContext(patch('agent_manager.integrations.opencode.read', side_effect=read))

    def path(self):
        return service.context(self.manager, self.manager.resolve_profile('work'), self.base)[2]


@unittest.skipIf(os.name == 'nt', 'POSIX disposable executable fixture')
class IntegrationTests(IntegrationFixture, unittest.TestCase):
    def test_reviewed_integrations_are_distinct_from_native_support_and_auth(self):
        result = self.manager.integrations(cwd=self.base)
        rows = {row['id']: row for row in result['integrations']}
        self.assertEqual(result['schema'], 'agent-manager/integrations-2')
        self.assertTrue(rows['openai']['reviewed'])
        self.assertEqual(rows['openai']['active_connection']['method'], 'oauth')
        self.assertEqual(rows['openai']['budget']['reason'], 'reader-unavailable')
        self.assertEqual(rows['openrouter']['budget']['status'], 'partial')
        self.assertFalse(rows['unreviewed']['reviewed'])
        self.assertEqual(rows['unreviewed']['budget']['status'], 'unsupported')
        self.assertNotIn('fixture-secret', json.dumps(result))
        self.assertNotIn('fixture-secret', self.path().read_text())

    def test_openai_api_key_and_subscription_have_separate_unknown_allowances(self):
        self.native['data'][0]['connections'][0]['method'] = 'key'
        self.bridge['integrations'][0]['active_connection']['method'] = 'key'
        self.bridge['integrations'][0]['budget']['reason'] = 'api-billing-reader-unavailable'
        result = self.manager.budget(cwd=self.base, model='openai/native')
        self.assertEqual(result['status'], 'unknown')
        self.assertFalse(result['buckets'])
        self.assertEqual(result['selected_route']['auth_method'], 'key')
        self.assertIn('API-key billing', result['reason'])

    def test_models_from_different_creators_share_the_paying_openrouter_connection(self):
        first = self.manager.budget(cwd=self.base, model='openrouter/openai/native')
        second = self.manager.budget(cwd=self.base, model='openrouter/anthropic/other')
        self.assertEqual(first['scope'], 'provider-connection')
        self.assertEqual(first['selected_route']['integration_id'], 'openrouter')
        self.assertEqual(first['selected_route']['connection_id'], second['selected_route']['connection_id'])
        self.assertEqual(first['buckets'], second['buckets'])
        self.assertEqual(first['buckets'][0]['remaining'], 75)
        self.assertEqual(first['buckets'][1]['status'], 'unknown')
        self.assertEqual(self.probe.call_count, 1)
        self.assertEqual(self.model_probe.call_count, 1)

    def test_default_model_and_saved_model_choose_the_correct_integration(self):
        self.assertEqual(self.manager.budget(cwd=self.base)['selected_model'], 'openrouter/openai/native')
        self.manager.set_model('work', 'openai/native')
        result = self.manager.budget(cwd=self.base)
        self.assertEqual(result['selected_route']['integration_id'], 'openai')
        self.assertEqual(result['status'], 'unknown')
        self.assertFalse(result['buckets'])

    def test_custom_runtime_or_gateway_never_receives_a_reviewed_budget(self):
        for change in ('package', 'endpoint'):
            with self.subTest(change=change):
                route = self.bridge['providers'][1]
                route['package'] = 'custom-runtime' if change == 'package' else '@opencode/ai/providers/openrouter'
                route['endpoint_reviewed'] = change == 'package'
                result = self.manager.budget(cwd=self.base, refresh=True)
                self.assertEqual(result['status'], 'unsupported')
                self.assertFalse(result['buckets'])

    def test_paying_integration_comes_from_native_routing_not_the_model_prefix(self):
        self.bridge['providers'][0].update(integration_id='openrouter', package='@opencode/ai/providers/openrouter')
        result = self.manager.budget(cwd=self.base, model='openai/native')
        self.assertEqual(result['selected_route']['integration_id'], 'openrouter')
        self.assertEqual(result['buckets'][0]['remaining'], 75)

    def test_alias_rename_and_concurrent_reads_share_one_five_minute_snapshot(self):
        self.manager.alias_profile('short', 'work')
        with ThreadPoolExecutor(max_workers=3) as workers:
            results = list(workers.map(lambda name: self.manager.integrations(name, cwd=self.base), ['work', 'short', 'work']))
        self.assertEqual(self.probe.call_count, 1)
        self.assertEqual(len({row['observed_at'] for row in results}), 1)
        self.manager.rename('short', 'renamed')
        result = self.manager.integrations('renamed', cwd=self.base)
        self.assertEqual(result['profiles'], ['renamed', 'work'])
        self.assertEqual(self.path().stat().st_mode & 0o777, 0o600)

    def test_expiry_is_five_minutes_while_models_keep_the_daily_gate(self):
        now = time.time()
        with patch('agent_manager.integrations.service.time.time', return_value=now): self.manager.integrations(cwd=self.base)
        with patch('agent_manager.integrations.service.time.time', return_value=now + 299):
            self.assertTrue(self.manager.integrations(cwd=self.base)['cached'])
        with patch('agent_manager.integrations.service.time.time', return_value=now + 300):
            self.assertEqual(self.manager.integrations(cwd=self.base, cached=True)['status'], 'stale')
            self.assertFalse(self.manager.integrations(cwd=self.base)['cached'])
        self.assertEqual(self.probe.call_count, 2)
        self.assertEqual(self.model_probe.call_count, 1)

    def test_failed_refresh_keeps_only_stale_matching_observations(self):
        first = self.manager.integrations(cwd=self.base)
        self.probe.side_effect = ModelError('timeout')
        result = self.manager.integrations(cwd=self.base, refresh=True)
        self.assertEqual(result['status'], 'stale')
        self.assertEqual(result['observed_at'], first['observed_at'])
        self.assertEqual(result['integrations'][1]['budget']['status'], 'stale')
        self.assertTrue(all(row['status'] == 'stale' for row in result['integrations'][1]['budget']['buckets']))
        self.manager.integrations(cwd=self.base)
        self.assertEqual(self.probe.call_count, 2)

    def test_fixed_rpc_diagnostics_survive_cache_reads_without_native_messages(self):
        self.probe.side_effect = schema.IntegrationError('rpc-invalid-input')
        first = self.manager.integrations(cwd=self.base)
        cached = self.manager.integrations(cwd=self.base, cached=True)
        self.assertEqual(first['error']['code'], 'rpc-invalid-input')
        self.assertEqual(cached['error'], first['error'])
        self.assertEqual(self.probe.call_count, 1)

    def test_source_changes_cannot_reuse_an_old_connection_budget(self):
        self.manager.integrations(cwd=self.base)
        config = self.manager.home('work') / 'config/opencode/opencode.json'
        config.parent.mkdir(parents=True, exist_ok=True); config.write_text('{}')
        self.probe.side_effect = ModelError('timeout')
        result = self.manager.integrations(cwd=self.base)
        self.assertFalse(result['integrations'])
        self.assertIsNone(result['observed_at'])

    def test_native_account_change_invalidates_model_snapshots(self):
        self.manager.integrations(cwd=self.base)
        self.native['data'][1]['connections'][0]['id'] = 'cred_replacement'
        self.bridge['integrations'][1]['active_connection']['id'] = 'cred_replacement'
        result = self.manager.integrations(cwd=self.base, refresh=True)
        self.assertEqual(result['models_status'], 'unknown')
        self.assertFalse(result['routes'][1]['models'])
        self.manager.models(cwd=self.base)
        self.assertEqual(self.model_probe.call_count, 2)

    def test_bridge_outage_does_not_infer_an_account_change(self):
        self.manager.integrations(cwd=self.base)
        self.bridge = None
        result = self.manager.integrations(cwd=self.base, refresh=True)
        self.assertFalse(result['bridge_available'])
        self.assertEqual(result['models_status'], 'ok')
        self.assertTrue(all(row['budget']['reason'] in ('bridge-unavailable', 'unreviewed-integration') for row in result['integrations']))

    def test_login_attempt_and_environment_retirement_discard_cache(self):
        result = self.manager.integrations(cwd=self.base)
        path = self.path()
        with patch.object(self.manager, '_invoke_native', return_value=1): self.manager.login('work')
        self.assertFalse(path.exists())
        self.manager.integrations(cwd=self.base)
        delete_profile(self.manager, 'work')
        self.manager.forget_environment(result['environment_id'])
        self.assertFalse(path.exists())

    def test_unrecognized_future_or_secret_cache_fields_are_rejected(self):
        self.manager.integrations(cwd=self.base)
        path = self.path(); original = path.read_text()
        for transform in (lambda row: row['data']['integrations'][1].update(secret='fixture-secret'),
                lambda row: row.update(checked_at=time.time() + 100),
                lambda row: row['data']['integrations'][1]['budget'].update(observed_at=time.time() + 100)):
            value = json.loads(original); transform(value); path.write_text(json.dumps(value))
            result = self.manager.integrations(cwd=self.base, cached=True)
            self.assertEqual(result['status'], 'unknown')
            self.assertFalse(result['integrations'])

    def test_missing_budget_never_becomes_unlimited_and_does_not_enable_automatic_selection(self):
        self.bridge['integrations'][1]['budget']['observation'].update(limit=None, remaining=None)
        result = self.manager.budget(cwd=self.base)
        self.assertFalse(any(row['unlimited'] for row in result['buckets']))
        from agent_manager.budgets.ranking import Ineligible, score
        with self.assertRaises(Ineligible): score(result, self.manager.provider_spec.budget)
        from agent_manager.profiles.selection import choose
        before = self.probe.call_count
        self.assertEqual(choose(self.manager).target, 'work')
        self.assertEqual(self.probe.call_count, before + 1)

    def test_projects_have_distinct_connection_observations(self):
        self.manager.integrations(cwd=self.base)
        other = self.base / 'project'; other.mkdir()
        self.manager.integrations(cwd=other)
        self.assertEqual(self.probe.call_count, 2)


@unittest.skipIf(os.name == 'nt', 'POSIX disposable executable fixture')
class NativeProbeTests(BudgetFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.manager = self.managers['opencode']
        self.policy = load_registry(self.config).resolve('opencode').integrations

    def test_codex_dependency_uses_configured_executable_and_manifest_versions_without_auth_io(self):
        with patch.dict(os.environ, AGENT_MANAGER_OPENAI_CODEX='untrusted-parent-setting'), \
                patch('subprocess.Popen', side_effect=AssertionError('No subprocess during dependency resolution')):
            overrides = opencode.overrides(self.manager, self.manager.home('work'), self.base, self.policy)
        value = json.loads(overrides['AGENT_MANAGER_OPENAI_CODEX'])
        codex = self.managers['codex']
        self.assertEqual(value['executable'], str(Path(codex.config['executable']).resolve()))
        self.assertEqual(value['versions'], list(codex.provider_spec.budget.versions))

    @unittest.skipUnless(shutil.which('node'), 'Node is needed for the bundled native plugin fixture')
    def test_shipped_native_probe_with_compiled_service_rules(self):
        result = subprocess.run(['node', str(Path(__file__).parent / 'fixtures/opencode_budget_probe.mjs')],
            input=json.dumps([row.native() for row in self.policy.rules]), text=True,
            capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed to import the shipped plugin package')
    def test_plugin_package_imports_from_an_isolated_directory_without_sdk_dependencies(self):
        package = self.base / 'standalone-plugin'
        shutil.copytree(opencode.NATIVE, package)
        script = '''import { pathToFileURL } from 'node:url';
const { default: plugin } = await import(pathToFileURL(process.argv[1]));
if (plugin.id !== 'aixand.agent.budget-probe' || typeof plugin.setup !== 'function')
  throw new Error('Invalid native plugin entrypoint');'''
        result = subprocess.run(['node', '--input-type=module', '-e', script, str(package / 'index.js')],
            cwd=self.base, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_native_protocol_uses_public_inventory_and_private_budget_rpc_only(self):
        calls = []
        def request(path, body=None):
            calls.append((path, body))
            if path == '/api/integration': return native_fixture()
            if path == '/api/rpc/aixand.agent.budgets.v2/read': return {'output': bridge_fixture()}
            self.fail('Unreviewed native request: ' + path)
        @contextmanager
        def server(manager, home, overrides, **options):
            value = json.loads(overrides['OPENCODE_CONFIG_CONTENT'])
            self.assertEqual(value['model'], 'openai/example')
            self.assertIn(str(opencode.NATIVE.resolve()), value['plugins'])
            self.assertEqual(home, self.manager.home('work'))
            self.assertEqual(options['cwd'], self.base)
            yield request
        with patch('agent_manager.integrations.opencode.metadata_server', server), patch.dict(os.environ,
                OPENCODE_CONFIG_CONTENT='{"model":"openai/example","plugins":["existing-plugin"]}'):
            native, bridge, version = opencode.read(self.manager, self.manager.home('work'), self.base, self.policy)
        self.assertEqual(version, '2.0.18')
        self.assertEqual(calls[1][1], {'input': {'rules': [row.native() for row in self.policy.rules]}})
        self.assertEqual(len(calls), 2)
        self.assertFalse(list(self.manager.home('work').rglob('opencode.json')))

    def test_bad_rpc_requests_are_not_reported_as_missing_plugins_or_retried(self):
        for kind, expected in (('rpc.invalid_input', 'rpc-invalid-input'),
                ('rpc.method_not_found', 'rpc-method-not-found'), ('unexpected', 'rpc-rejected')):
            calls = []
            @contextmanager
            def server(*args, **kwargs):
                def request(path, body=None):
                    if body is None: return native_fixture()
                    calls.append(path)
                    raise HTTPError('http://127.0.0.1/rpc', 400, 'Bad request', {},
                        io.BytesIO(json.dumps(dict(type=kind, message='fixture-secret')).encode()))
                yield request
            with self.subTest(kind=kind), patch('agent_manager.integrations.opencode.metadata_server', server):
                with self.assertRaises(schema.IntegrationError) as error:
                    opencode.read(self.manager, self.manager.home('work'), self.base, self.policy)
                self.assertEqual(error.exception.code, expected)
                self.assertNotIn('fixture-secret', str(error.exception))
                self.assertEqual(len(calls), 1)

    def test_only_an_unavailable_rpc_is_retried_while_registration_settles(self):
        calls = []
        @contextmanager
        def server(*args, **kwargs):
            def request(path, body=None):
                if body is None: return native_fixture()
                calls.append(path)
                if len(calls) == 1:
                    raise HTTPError('http://127.0.0.1/rpc', 400, 'Bad request', {},
                        io.BytesIO(b'{"type":"rpc.unavailable","message":"fixture-secret"}'))
                return {'output': bridge_fixture()}
            yield request
        with patch('agent_manager.integrations.opencode.metadata_server', server):
            self.assertIsNotNone(opencode.read(self.manager, self.manager.home('work'), self.base, self.policy)[1])
        self.assertEqual(len(calls), 2)

    def test_unknown_versions_do_not_start_a_native_metadata_server(self):
        with patch.dict(os.environ, BUDGET_FIXTURE_VERSION='999.0.0'), patch('agent_manager.integrations.opencode.metadata_server') as server:
            with self.assertRaises(ModelError) as error: opencode.read(self.manager, self.manager.home('work'), self.base, self.policy)
        self.assertEqual(error.exception.code, 'unsupported'); server.assert_not_called()

    def test_native_version_prefix_is_accepted(self):
        @contextmanager
        def server(*args, **kwargs):
            yield lambda path, body=None: native_fixture() if path == '/api/integration' else {'output': bridge_fixture()}
        with patch.dict(os.environ, BUDGET_FIXTURE_VERSION='v2.0.18'), patch('agent_manager.integrations.opencode.metadata_server', server):
            self.assertEqual(opencode.read(self.manager, self.manager.home('work'), self.base, self.policy)[2], '2.0.18')

    def test_inline_configuration_is_not_overwritten_or_echoed_on_invalid_input(self):
        with patch.dict(os.environ, OPENCODE_CONFIG_CONTENT='fixture-secret-invalid-json'):
            with self.assertRaises(Error) as error: opencode.overrides(self.manager, self.manager.home('work'), self.base)
        self.assertNotIn('fixture-secret', str(error.exception))


@unittest.skipIf(os.name == 'nt', 'POSIX disposable executable fixture')
class IntegrationInterfaceTests(IntegrationFixture, unittest.IsolatedAsyncioTestCase):
    async def test_cli_sdk_and_mcp_share_public_metadata_and_budget_context(self):
        client = Client('opencode', config=self.config, root=self.root, runtime=object())
        first = await client.integrations('work', cwd=self.base)
        budget = await client.budget('work', cwd=self.base, model='openrouter/openai/native', cached=True)
        self.assertEqual(budget['status'], 'partial')
        with redirect_stdout(io.StringIO()) as output:
            execute(['--config', str(self.config), '--home', str(self.root), 'opencode', 'integrations',
                '--profile', 'work', '--cwd', str(self.base), '--cached', '--json'])
        self.assertEqual(json.loads(output.getvalue())['observed_at'], first['observed_at'])
        with redirect_stdout(io.StringIO()) as output:
            execute(['--config', str(self.config), '--home', str(self.root), 'opencode', 'budget', 'work',
                '--cwd', str(self.base), '--model', 'openai/native', '--cached', '--json'])
        self.assertEqual(json.loads(output.getvalue())['status'], 'unknown')
        result = await LocalBackend(config=self.config, home=self.root).execute('get_integrations',
            {'provider': 'opencode', 'profile': 'work', 'cwd': str(self.base), 'cached': True}, 'read')
        self.assertEqual(result['integrations']['observed_at'], first['observed_at'])
        Management(self.manager).execute('get_integrations', {'profile': 'work', 'cwd': str(self.base), 'cached': True}, request_id='read')
        self.assertFalse((self.manager.root / 'profile-operations').exists())

    async def test_cached_only_never_starts_model_or_integration_probes(self):
        self.assertEqual(self.manager.integrations(cwd=self.base, cached=True)['status'], 'unknown')
        self.assertEqual(self.manager.budget(cwd=self.base, cached=True)['status'], 'unknown')
        self.probe.assert_not_called(); self.model_probe.assert_not_called()
        for method in (self.manager.integrations, self.manager.budget):
            with self.assertRaises(Error): method(refresh=True, cached=True)

    async def test_management_schema_rejects_tokens_and_conflicting_cache_flags(self):
        validate('get_integrations', {'provider': 'opencode', 'cached': True})
        for arguments in ({'provider': 'opencode', 'token': 'fixture-secret'}, {'provider': 'opencode', 'refresh': True, 'cached': True}):
            with self.assertRaises(Error): validate('get_integrations', arguments)


class IntegrationManifestTests(unittest.TestCase):
    def test_provider_policy_accepts_service_references_only(self):
        import tomllib
        from agent_manager.providers.schema import ManifestError, parse_definition
        original = tomllib.loads(Path('agent_manager/providers/definitions/opencode.toml').read_text())
        for transform in (lambda value: value.update(adapter='arbitrary-import'),
                lambda value: value.update(versions=['latest']), lambda value: value.update(timeout=True),
                lambda value: value.update(cache_ttl=301),
                lambda value: value.update(services=['openai', 'openai']),
                lambda value: value.update(services='openai'),
                lambda value: value.update(reviewed={'token': 'fixture-secret'})):
            document = copy.deepcopy(original); transform(document['terminal']['integrations'])
            with self.assertRaises(ManifestError) as error: parse_definition(document)
            self.assertNotIn('fixture-secret', str(error.exception))
