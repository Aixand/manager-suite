"""Offline native subprocesses, environment cache lifecycle, CLI, SDK and MCP."""
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_manager import Client, Error, Manager
from agent_manager.budgets.models import BudgetError, bucket
from agent_manager.interfaces.cli.dispatch import execute
from agent_manager.interfaces.management_contract import TOOLS, validate
from agent_manager.interfaces.management_mcp import LocalBackend
from agent_manager.profiles.operations import Management, delete_profile
from agent_manager.providers.schema import ManifestError, parse_definition
from tests.support import FakeRuntime
import tomllib


class BudgetFixture:
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='agent-budget-test-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        user = self.base / 'user'; user.mkdir()
        self.config = self.base / 'providers.toml'
        self.config.write_text('')
        environment = patch.dict(os.environ, {'HOME': str(user), 'USERPROFILE': str(user),
            'AGENT_MANAGER_CONFIG': str(self.config), 'BUDGET_FIXTURE_MODE': '', 'BUDGET_FIXTURE_VERSION': ''})
        environment.start(); self.addCleanup(environment.stop)
        os.environ.pop('BUDGET_FIXTURE_VERSION')
        self.root = self.base / 'manager'
        self.managers = {}
        for provider in ('codex', 'claude', 'copilot', 'antigravity', 'opencode'):
            program = self.base / provider
            program.write_text('#!' + sys.executable + '\n' +
                (Path(__file__).parent / 'fixtures/budget_provider.py').read_text())
            program.chmod(0o700)
            with self.config.open('a') as stream:
                stream.write(f'[providers.{provider}]\nexecutable={json.dumps(str(program))}\n')
            manager = Manager(provider, self.config, self.root)
            if provider == 'opencode':
                # This fixture covers provider-account readers. Integration
                # probes have their own disposable native fixtures.
                from agent_manager.integrations.policy import IntegrationPolicy
                manager.provider_spec = replace(manager.provider_spec, integrations=IntegrationPolicy())
            manager.new_profile('work'); manager.set_default('work')
            self.managers[provider] = manager
        self.manager = self.managers['codex']

    def cache(self, manager=None):
        manager = manager or self.manager
        identifier = manager.resolve_profile('work').environment_id
        return manager.root / 'budgets' / (identifier + '.json')

    def probes(self, manager=None):
        manager = manager or self.manager
        path = manager.home('work') / 'probe-log.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@unittest.skipIf(os.name == 'nt', 'POSIX executable fixture; native Windows qualification remains separate')
class NativeBudgetTests(BudgetFixture, unittest.TestCase):
    def test_every_declared_version_uses_its_reviewed_wire_format(self):
        for provider, manager in self.managers.items():
            for version in manager.provider_spec.budget.versions:
                with self.subTest(provider=provider, version=version), patch.dict(os.environ, BUDGET_FIXTURE_VERSION=version):
                    self.assertEqual(manager.budget(refresh=True)['status'], 'ok')

    def test_all_four_native_interfaces_select_the_home_and_filter_ambient_credentials(self):
        with patch.dict(os.environ, dict(OPENAI_API_KEY='fixture', ANTHROPIC_API_KEY='fixture',
                GH_TOKEN='fixture', GEMINI_API_KEY='fixture')):
            for provider, manager in self.managers.items():
                if provider == 'opencode': continue
                with self.subTest(provider=provider):
                    result = manager.budget()
                    self.assertEqual(result['status'], 'ok')
                    self.assertEqual(result['scope'], 'provider-account')
                    self.assertEqual(result['buckets'][0]['remaining_percent'], 75)
                    self.assertFalse(result['cached'])
                    probe = self.probes(manager)[0]
                    self.assertEqual(probe['home'], str(manager.home('work')))
                    self.assertFalse(probe['ambient_credentials'])
                    self.assertNotIn('fixture', self.cache(manager).read_text())
        methods = [json.loads(line) for line in (self.manager.home('work') / 'methods.jsonl').read_text().splitlines()]
        self.assertEqual(methods, ['initialize', 'initialized', 'account/rateLimits/read'])
        copilot = self.managers['copilot']
        methods = [json.loads(line) for line in (copilot.home('work') / 'methods.jsonl').read_text().splitlines()]
        self.assertEqual(methods, ['status.get', 'account.getQuota'])

    def test_aliases_share_cache_and_rename_preserves_observation_identity(self):
        self.manager.alias_profile('short', 'work')
        first = self.manager.budget('work')
        second = self.manager.budget('short')
        self.assertTrue(second['cached'])
        self.assertEqual(first['environment_id'], second['environment_id'])
        self.assertEqual(first['observed_at'], second['observed_at'])
        self.assertEqual(len(self.probes()), 1)
        self.manager.rename('short', 'renamed')
        self.assertEqual(self.manager.budget('renamed')['profiles'], ['renamed', 'work'])

    def test_alias_rename_during_query_cannot_redirect_the_environment(self):
        def read(manager, home, policy):
            manager.rename('work', 'renamed')
            return [bucket('window', 'Window', remaining_percent=50)]
        with patch('agent_manager.budgets.adapters.read', side_effect=read):
            result = self.manager.budget('work')
        self.assertEqual(result['profiles'], ['renamed'])
        self.assertEqual(result['environment_id'], self.manager.resolve_profile('renamed').environment_id)

    def test_all_environments_include_unnamed_homes_without_duplicate_alias_queries(self):
        self.manager.alias_profile('short', 'work')
        self.manager.new_profile('other')
        delete_profile(self.manager, 'other')
        rows = self.manager.budgets()
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({row['environment_id'] for row in rows}), 2)
        self.assertEqual(sorted(len(row['profiles']) for row in rows), [0, 2])
        self.assertTrue(all('total' not in row for row in rows))

    def test_cached_only_skips_native_probes_and_opencode_budget_is_unsupported(self):
        self.assertEqual(self.manager.budget(cached=True)['status'], 'unknown')
        self.assertFalse(self.probes())
        self.assertEqual(self.managers['opencode'].budget(refresh=True)['status'], 'unsupported')
        self.assertFalse(self.probes(self.managers['opencode']))
        with self.assertRaises(Error): self.manager.budget(refresh=True, cached=True)
        with self.assertRaises(Error): self.manager.budgets(refresh=True, cached=True)

    def test_concurrent_alias_queries_share_one_native_observation(self):
        from concurrent.futures import ThreadPoolExecutor
        self.manager.alias_profile('short', 'work')
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.manager.budget, 'work')
            second = pool.submit(self.manager.budget, 'short')
            results = [first.result(timeout=5), second.result(timeout=5)]
        self.assertEqual(results[0]['observed_at'], results[1]['observed_at'])
        self.assertEqual(len(self.probes()), 1)

    def test_expired_cache_and_reset_are_stale_without_fabricating_a_new_allowance(self):
        self.manager.budget()
        path = self.cache(); value = json.loads(path.read_text())
        value['observed_at'] = time.time() - 301
        path.write_text(json.dumps(value))
        result = self.manager.budget(cached=True)
        self.assertEqual(result['status'], 'stale')
        self.assertEqual(result['buckets'][1]['remaining_percent'], 0)
        self.assertTrue(all(row['status'] == 'stale' for row in result['buckets']))
        self.manager.budget(refresh=True)
        value = json.loads(path.read_text()); value['buckets'][0]['resets_at'] = 1
        path.write_text(json.dumps(value))
        self.assertEqual(self.manager.budget(cached=True)['status'], 'stale')
        self.assertFalse(self.manager.budget()['cached'])

    def test_refresh_failure_retains_stale_data_but_auth_failure_discards_it(self):
        first = self.manager.budget()
        with patch('agent_manager.budgets.adapters.read', side_effect=BudgetError('timeout')):
            result = self.manager.budget(refresh=True)
        self.assertEqual(result['status'], 'stale')
        self.assertEqual(result['observed_at'], first['observed_at'])
        self.assertEqual(self.manager.budget(cached=True)['status'], 'stale')
        with patch.dict(os.environ, BUDGET_FIXTURE_MODE='auth-error'):
            result = self.manager.budget(refresh=True)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['error']['code'], 'authentication')
        self.assertFalse(result['buckets'])
        self.assertFalse(self.cache().exists())
        self.assertNotIn('private-fixture-token', json.dumps(result))

    def test_manager_login_observation_invalidates_the_cached_account(self):
        self.manager.budget()
        target = self.manager.resolve_profile('work')
        state = self.manager.state()
        state['login_status'] = {target.environment_id: {'state': 'signed-out', 'checked_at': time.time()}}
        self.manager.save(state)
        self.assertEqual(self.manager.budget(cached=True)['status'], 'unknown')
        self.assertFalse(self.manager.budget()['cached'])
        self.assertEqual(len(self.probes()), 2)

    def test_even_a_failed_native_login_attempt_discards_old_account_observations(self):
        self.manager.budget()
        with patch.object(self.manager, '_invoke_native', return_value=1):
            self.assertEqual(self.manager.login('work'), 1)
        self.assertFalse(self.cache().exists())
        self.assertEqual(self.manager.budget(cached=True)['status'], 'unknown')

    def test_retiring_an_environment_discards_its_disposable_observation(self):
        result = self.manager.budget(); path = self.cache()
        delete_profile(self.manager, 'work')
        self.manager.forget_environment(result['environment_id'])
        self.assertFalse(path.exists())

    def test_invalid_and_future_cache_entries_are_not_published(self):
        self.manager.budget(); path = self.cache()
        original = json.loads(path.read_text())
        for transform in (lambda value: value.update(observed_at=time.time() + 1000),
                          lambda value: value['buckets'][0].update(token='private')):
            value = json.loads(json.dumps(original)); transform(value); path.write_text(json.dumps(value))
            result = self.manager.budget(cached=True)
            self.assertEqual(result['status'], 'unknown')
            self.assertNotIn('private', json.dumps(result))

    def test_timeout_output_limits_and_version_guards_are_bounded(self):
        policy = replace(self.manager.provider_spec.budget, timeout=1)
        self.manager.provider_spec = replace(self.manager.provider_spec, budget=policy)
        for mode, expected in (('timeout', 'timeout'), ('oversized', 'invalid-response')):
            started = time.monotonic()
            with patch.dict(os.environ, BUDGET_FIXTURE_MODE=mode): result = self.manager.budget(refresh=True)
            self.assertEqual(result['error']['code'], expected)
            self.assertLess(time.monotonic() - started, 4)
        before = len(self.probes())
        with patch.dict(os.environ, BUDGET_FIXTURE_VERSION='999.0.0'):
            result = self.manager.budget(refresh=True)
        self.assertEqual(result['status'], 'unsupported')
        self.assertEqual(len(self.probes()), before)

    def test_model_response_is_rejected_and_does_not_write_a_cache(self):
        manager = self.managers['claude']
        with patch.dict(os.environ, BUDGET_FIXTURE_MODE='model-output'):
            result = manager.budget()
        self.assertEqual(result['error']['code'], 'invalid-response')
        self.assertFalse(self.cache(manager).exists())

    def test_cli_supports_refresh_cached_json_and_all_environments(self):
        with redirect_stdout(io.StringIO()) as output:
            code = execute(['--config', str(self.config), '--home', str(self.root), 'codex', 'budget', 'work', '--refresh', '--json'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['status'], 'ok')
        with redirect_stdout(io.StringIO()) as output:
            execute(['--config', str(self.config), '--home', str(self.root), 'codex', 'budget', '--all', '--cached', '--json'])
        self.assertEqual(len(json.loads(output.getvalue())), 1)
        with self.assertRaises(Error):
            execute(['--config', str(self.config), '--home', str(self.root), 'codex', 'budget', 'work', '--all'])

    def test_management_budget_reads_never_create_operation_receipts(self):
        import uuid
        result = Management(self.manager).execute('get_budget', {'profile': 'work', 'cached': True}, request_id=str(uuid.uuid4()))
        self.assertEqual(result['budget']['status'], 'unknown')
        self.assertFalse((self.manager.root / 'profile-operations').exists())


@unittest.skipIf(os.name == 'nt', 'POSIX executable fixture')
class AsyncBudgetInterfaces(BudgetFixture, unittest.IsolatedAsyncioTestCase):
    async def test_sdk_and_local_mcp_return_the_shared_observation(self):
        client = Client('codex', config=self.config, root=self.root, runtime=FakeRuntime())
        first = await client.budget('work')
        rows = await client.budgets(cached=True)
        self.assertEqual(rows[0]['observed_at'], first['observed_at'])
        backend = LocalBackend(config=self.config, home=self.root)
        result = await backend.execute('get_budget', {'provider': 'codex', 'profile': 'work', 'cached': True}, 'read')
        self.assertEqual(result['budget']['observed_at'], first['observed_at'])
        result = await backend.execute('list_budgets', {'provider': 'codex', 'cached': True}, 'read')
        self.assertEqual(len(result['budgets']), 1)


class BudgetContractTests(unittest.TestCase):
    def test_recommended_codex_version_has_a_qualified_budget_reader(self):
        from agent_manager.providers.terminal_policy import recommended
        from agent_manager.providers.config import load_registry
        policy = load_registry().resolve('codex').budget
        self.assertIn(recommended('codex'), policy.versions)

    def test_manifests_bind_readers_to_providers_and_validate_limits(self):
        original = tomllib.loads(Path('agent_manager/providers/definitions/codex.toml').read_text())
        for field, value in (('adapter', 'claude-usage'), ('versions', []), ('timeout', True),
                             ('timeout', 121), ('cache_ttl', 0), ('versions', ['latest']), ('token', 'private')):
            document = json.loads(json.dumps(original)); document['terminal']['budget'][field] = value
            with self.subTest(field=field), self.assertRaises(ManifestError) as caught:
                parse_definition(document)
            self.assertNotIn('private', str(caught.exception))

    def test_mcp_marks_budget_reads_and_rejects_secret_inputs(self):
        names = {row['name']: row for row in TOOLS}
        self.assertTrue(names['get_budget']['annotations']['readOnlyHint'])
        self.assertTrue(names['get_budget']['annotations']['openWorldHint'])
        validate('get_budget', {'provider': 'codex', 'cached': True})
        with self.assertRaises(Error): validate('get_budget', {'provider': 'codex', 'token': 'private'})
        with self.assertRaises(Error): validate('get_budget', {'provider': 'codex', 'refresh': True, 'cached': True})


if __name__ == '__main__': unittest.main()
