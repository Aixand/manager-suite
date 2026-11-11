"""Disposable native protocols, daily cache, schema and public interfaces."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stdout
from dataclasses import replace
import io
import json
import os
import sys
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from agent_manager import Client, Error
from agent_manager.interfaces.cli.dispatch import execute
from agent_manager.interfaces.management_contract import validate
from agent_manager.interfaces.management_mcp import LocalBackend
from agent_manager.models import adapters, service
from agent_manager.models.schema import ModelError, model, validate_models
from agent_manager.profiles.operations import Management, delete_profile
from tests.test_budget_interfaces import BudgetFixture


@unittest.skipIf(os.name == 'nt', 'POSIX disposable executable fixture')
class ModelAdapterTests(BudgetFixture, unittest.TestCase):
    def test_five_providers_share_the_same_envelope_without_model_turns(self):
        with patch('agent_manager.providers.opencode.catalog', return_value={'data': [
                dict(id='CaseSensitive', providerID='provider', modelID='upstream', name='Native')]}):
            for provider, manager in self.managers.items():
                with self.subTest(provider=provider):
                    result = manager.models('work', cwd=self.base)
                    self.assertEqual(result['schema'], 'agent-manager/models-1')
                    self.assertEqual(result['status'], 'ok')
                    self.assertTrue(result['models'])
                    self.assertEqual(result['cwd'], str(self.base.resolve()))
                    self.assertFalse((manager.home('work') / 'launched.json').exists())
                    self.assertFalse(self.probes(manager))
                    self.assertEqual(result['expires_at'], result['observed_at'] + 86400)

    def test_claude_fallback_is_explicit_alias_metadata_with_unknown_access(self):
        manager = self.managers['claude']
        (manager.home('work') / 'alias-fallback').touch()
        result = manager.models(cwd=self.base)
        self.assertEqual(result['source'], 'claude-aliases')
        rows = {row['id']: row for row in result['models']}
        self.assertEqual(rows['opusplan']['kind'], 'preset')
        self.assertEqual(rows['sonnet']['availability'], 'unknown')
        self.assertIsNone(rows['sonnet']['pricing'])
        self.assertFalse(manager.models(cwd=self.base, free=True)['models'])

    def test_allowlist_preserves_native_selector_tiers_and_unknown_fields(self):
        rows = adapters.opencode({'data': [dict(providerID='gateway', id='NativeAlias', modelID='ExactModel',
            settings={'token': 'secret'}, headers={'Authorization': 'secret'}, body={'key': 'secret'},
            limit={'context': 200000}, cost=[dict(input=0, output=0, cache={'read': 0}),
                dict(tier=dict(type='context', size=128000), input=4, output=12)],
            variants=[dict(id='high', settings={'key': 'secret'})])]})
        row = validate_models(rows)[0]
        self.assertEqual(row['id'], 'gateway/NativeAlias')
        self.assertEqual(row['resolved_id'], 'ExactModel')
        self.assertEqual(row['pricing'][1]['context_over'], 128000)
        self.assertIsNone(row['pricing'][0]['context_over'])
        self.assertEqual(row['variants'][0]['id'], 'high')
        self.assertIsNone(row['limits']['output_tokens'])
        self.assertIsNone(row['capabilities']['vision'])
        self.assertNotIn('secret', json.dumps(row))
        self.assertFalse(service.zero_price(row))
        with self.assertRaises(ModelError): validate_models([dict(id='valid', token='secret')])
        with self.assertRaises(ModelError): validate_models([model('same'), model('same')])

    def test_opencode_v2_model_info_uses_native_variants_modalities_and_cost_tiers(self):
        payload = {'data': [dict(id='gpt-native', modelID='gpt-upstream', providerID='openai',
            name='GPT Native', enabled=True, status='active', time=dict(released=0),
            capabilities=dict(tools=True, input=['text', 'image'], output=['text']),
            limit=dict(context=200000, input=160000, output=32000),
            cost=[dict(input=1, output=4, cache=dict(read=.1, write=0)),
                dict(tier=dict(type='context', size=200000), input=2, output=8, cache=dict(read=.2, write=0))],
            variants=[dict(id='low', settings=dict(reasoningEffort='low')),
                dict(id='high', body=dict(secret='private-fixture-value'))])]}
        row = adapters.opencode(payload)[0]
        self.assertEqual(validate_models([row]), [row])
        self.assertEqual(row['id'], 'openai/gpt-native')
        self.assertEqual(row['capabilities'], dict(tools=True, vision=True, reasoning=None))
        self.assertEqual(row['limits'], dict(context_tokens=200000, output_tokens=32000))
        self.assertEqual([v['id'] for v in row['variants']], ['low', 'high'])
        self.assertEqual(row['pricing'][1]['context_over'], 200000)
        self.assertNotIn('private-fixture-value', json.dumps(row))
        with patch('agent_manager.providers.opencode.catalog', return_value=payload):
            snapshot = self.managers['opencode'].models(cwd=self.base)
        self.assertEqual(snapshot['status'], 'ok')
        self.assertEqual(snapshot['models'], [row])
        text_only = adapters.opencode({'data': [dict(id='text-only', providerID='native',
            enabled=False, variants=[], capabilities=dict(tools=False, input=['text'], output=['text']))]})[0]
        self.assertEqual(text_only['availability'], 'restricted')
        self.assertEqual(text_only['capabilities'], dict(tools=False, vision=False, reasoning=None))
        self.assertEqual(text_only['variants'], [])

    def test_opencode_rejects_unrecognized_variant_and_modality_shapes(self):
        for fields in (dict(variants={'high': {}}), dict(capabilities=dict(input='image'))):
            with self.subTest(fields=fields), self.assertRaises(ModelError):
                adapters.opencode({'data': [dict(id='native', providerID='openai', **fields)]})

    def test_copilot_policy_limits_and_multiplier_are_not_free_allowance(self):
        row = adapters.copilot([dict(id='exact', policy={'state': 'disabled'}, billing={'multiplier': 0},
            capabilities={'supports': {'vision': True}, 'limits': {'max_context_window_tokens': 1000}})])[0]
        self.assertEqual(row['availability'], 'restricted')
        self.assertTrue(row['capabilities']['vision'])
        self.assertEqual(row['pricing'][0]['unit'], 'premium-request-multiplier')
        self.assertFalse(service.zero_price(row))

    def test_copilot_reported_token_price_tiers_keep_unknown_billing_unit(self):
        row = adapters.copilot([dict(id='exact', billing={'tokenPrices': dict(batchSize=1000,
            inputPrice=2, outputPrice=4, longContext=dict(inputPrice=6, outputPrice=8, maxPromptTokens=200000))})])[0]
        self.assertEqual([tier['input'] for tier in row['pricing']], [2, 6])
        self.assertTrue(all(tier['unit'] is None and tier['batch_size'] == 1000 for tier in row['pricing']))
        self.assertFalse(service.zero_price(row))

    def test_opencode_format_errors_discard_prior_good_models(self):
        from agent_manager.providers.opencode import CatalogError
        manager = self.managers['opencode']
        with patch('agent_manager.providers.opencode.catalog', return_value={'data': [dict(id='Exact', providerID='native')]}):
            self.assertEqual(manager.models(cwd=self.base)['status'], 'ok')
        with patch('agent_manager.providers.opencode.catalog', side_effect=CatalogError('invalid-response', 'Invalid OpenCode model catalog')):
            result = manager.models(cwd=self.base, refresh=True)
        self.assertEqual(result['error']['code'], 'invalid-response')
        self.assertFalse(result['models'])
        self.assertIsNone(result['observed_at'])

    def test_unknown_cli_versions_do_not_query_model_interfaces(self):
        with patch.dict(os.environ, BUDGET_FIXTURE_VERSION='999.0.0'):
            result = self.manager.models(cwd=self.base)
        self.assertEqual(result['status'], 'unsupported')
        self.assertFalse(result['models'])
        self.assertFalse((self.manager.home('work') / 'model-probes.jsonl').exists())

    def test_pagination_completes_and_repeated_cursors_are_rejected(self):
        for repeats in (False, True):
            calls = []
            def request(method, params=None, **options):
                calls.append(method)
                if method != 'model/list': return {}
                if params.get('cursor') is None: return {'data': [{'id': 'first'}], 'nextCursor': 'next'}
                return {'data': [{'id': 'second'}], 'nextCursor': 'next' if repeats else None}
            @contextmanager
            def rpc(*args, **kwargs): yield request
            with patch('agent_manager.models.adapters.run', return_value=(b'0.164.0', 0)), patch('agent_manager.models.adapters.rpc', rpc):
                result = self.manager.models(cwd=self.base, refresh=True)
            self.assertEqual(result['status'], 'unavailable' if repeats else 'ok')
            self.assertEqual(calls, ['initialize', 'initialized', 'model/list', 'model/list'])
            self.assertEqual([row['id'] for row in result['models']], [] if repeats else ['first', 'second'])

    def test_native_errors_are_sanitized_and_failed_attempts_are_not_retried(self):
        with patch.dict(os.environ, BUDGET_FIXTURE_MODE='auth-error'):
            result = self.manager.models(cwd=self.base)
        self.assertEqual(result['error']['code'], 'authentication')
        self.assertNotIn('private-fixture-token', json.dumps(result))
        with patch('agent_manager.models.adapters.read', side_effect=AssertionError('native probe')):
            self.assertEqual(self.manager.models(cwd=self.base)['checked_at'], result['checked_at'])

    def test_native_timeout_and_output_limits_are_bounded(self):
        spec = self.manager.provider_spec
        policy = replace(spec.models.discovery, timeout=1)
        self.manager.provider_spec = replace(spec, models=replace(spec.models, discovery=policy))
        # Version reads use the same bounded transport as the metadata RPC.
        program = Path(self.manager.config['executable'])
        for source, expected in (("import time; time.sleep(10)", 'timeout'),
                ("print('x' * (5 * 1024 * 1024))", 'invalid-response')):
            program.write_text('#!' + sys.executable + '\n' + source + '\n')
            started = time.monotonic()
            result = self.manager.models(cwd=self.base, refresh=True)
            self.assertEqual(result['error']['code'], expected)
            self.assertLess(time.monotonic() - started, 4)


@unittest.skipIf(os.name == 'nt', 'POSIX disposable executable fixture')
class ModelCacheTests(BudgetFixture, unittest.TestCase):
    def path(self):
        target = self.manager.resolve_profile('work')
        return service.context(self.manager, target, self.base)[2]

    def test_daily_boundary_force_refresh_and_aliases_share_one_query(self):
        now = time.time()
        with patch('agent_manager.models.adapters.read', return_value=([model('native')], 'codex-models', '0.164.0')) as read:
            with patch('agent_manager.models.service.time.time', return_value=now):
                first = self.manager.models(cwd=self.base)
            self.manager.alias_profile('short', 'work')
            with patch('agent_manager.models.service.time.time', return_value=now + 86399):
                second = self.manager.models('short', cwd=self.base)
                self.assertTrue(second['cached'])
                self.assertEqual(read.call_count, 1)
            with patch('agent_manager.models.service.time.time', return_value=now + 86400):
                self.assertEqual(self.manager.models(cwd=self.base, cached=True)['status'], 'stale')
                refreshed = self.manager.models(cwd=self.base)
                self.assertFalse(refreshed['cached'])
                self.assertEqual(read.call_count, 2)
                self.manager.models(cwd=self.base, refresh=True)
                self.assertEqual(read.call_count, 3)
        self.assertEqual(first['environment_id'], second['environment_id'])
        self.assertEqual(self.path().stat().st_mode & 0o777, 0o600)

    def test_failed_daily_refresh_keeps_stale_success_and_throttles_attempts(self):
        first = self.manager.models(cwd=self.base)
        with patch('agent_manager.models.adapters.read', side_effect=ModelError('timeout')) as read:
            result = self.manager.models(cwd=self.base, refresh=True)
            again = self.manager.models(cwd=self.base)
            self.assertEqual(read.call_count, 1)
        self.assertEqual(result['status'], 'stale')
        self.assertEqual(first['observed_at'], result['observed_at'])
        self.assertEqual(first['expires_at'], result['expires_at'])
        self.assertEqual(result['checked_at'], again['checked_at'])
        self.assertFalse(again['refresh_due'])

    def test_initial_and_authentication_failures_are_cached_without_old_account_data(self):
        self.manager.models(cwd=self.base)
        for code in ('authentication', 'invalid-response', 'unsupported'):
            with self.subTest(code=code), patch('agent_manager.models.adapters.read', side_effect=ModelError(code)) as read:
                failed = self.manager.models(cwd=self.base, refresh=True)
                again = self.manager.models(cwd=self.base)
                self.assertEqual(read.call_count, 1)
                self.assertIsNone(failed['observed_at'])
                self.assertEqual(again['models'], [])

    def test_alias_rename_does_not_invalidate_or_retarget_cache(self):
        first = self.manager.models(cwd=self.base)
        self.manager.rename('work', 'renamed')
        with patch('agent_manager.models.adapters.read', side_effect=AssertionError('native probe')):
            second = self.manager.models('renamed', cwd=self.base)
        self.assertEqual(second['observed_at'], first['observed_at'])
        self.assertEqual(second['profiles'], ['renamed'])

    def test_concurrent_queries_coalesce(self):
        def read(*args):
            time.sleep(.05)
            return [model('native')], 'codex-models', '0.164.0'
        with patch('agent_manager.models.adapters.read', side_effect=read) as probe:
            with ThreadPoolExecutor(max_workers=4) as workers:
                results = list(workers.map(lambda _: self.manager.models(cwd=self.base), range(4)))
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(len({row['observed_at'] for row in results}), 1)

    def test_project_config_and_cli_changes_invalidate_before_daily_expiry(self):
        self.manager.models(cwd=self.base)
        config = self.base / '.codex/config.toml'; config.parent.mkdir()
        config.write_text('model = "changed"\n')
        self.assertEqual(self.manager.models(cwd=self.base, cached=True)['status'], 'unknown')
        with patch('agent_manager.models.adapters.read', side_effect=ModelError('timeout')):
            result = self.manager.models(cwd=self.base)
        self.assertFalse(result['models'])
        self.assertIsNone(result['observed_at'])
        program = Path(self.manager.config['executable'])
        program.write_text(program.read_text() + '\n# replacement\n')
        self.assertEqual(self.manager.models(cwd=self.base, cached=True)['status'], 'unknown')

    def test_projects_have_distinct_cache_and_canonical_paths_share(self):
        first = self.manager.models(cwd=self.base)
        other = self.base / 'project'; other.mkdir()
        second = self.manager.models(cwd=other)
        self.assertFalse(second['cached'])
        self.assertTrue(self.manager.models(cwd=other / '..')['cached'])
        self.assertEqual(first['environment_id'], second['environment_id'])

    def test_login_attempt_and_retirement_remove_all_contexts(self):
        first = self.manager.models(cwd=self.base)
        path = self.path()
        with patch.object(self.manager, '_invoke_native', return_value=1): self.manager.login('work')
        self.assertFalse(path.exists())
        self.manager.models(cwd=self.base)
        delete_profile(self.manager, 'work')
        self.manager.forget_environment(first['environment_id'])
        self.assertFalse(path.exists())

    def test_corrupt_oversized_future_and_secret_cache_entries_are_rejected(self):
        self.manager.models(cwd=self.base)
        path = self.path(); original = path.read_text()
        for transform in (lambda r: r.update(checked_at=time.time() + 10),
                lambda r: r['models'][0].update(headers={'key': 'secret'}),
                lambda r: r.update(checked_at=True)):
            row = json.loads(original); transform(row); path.write_text(json.dumps(row))
            result = self.manager.models(cwd=self.base, cached=True)
            self.assertEqual(result['status'], 'unknown')
            self.assertNotIn('secret', json.dumps(result))
        path.write_bytes(b'x' * (service.MAX_CACHE + 1))
        self.assertEqual(self.manager.models(cwd=self.base, cached=True)['status'], 'unknown')

    def test_account_budget_refresh_does_not_probe_models(self):
        with patch('agent_manager.models.adapters.read', return_value=([model('native')], 'codex-models', '0.164.0')) as read:
            first = self.manager.budget()
            self.manager.budget(refresh=True)
            self.assertEqual(read.call_count, 0)
            self.assertEqual(first['status'], 'ok')
            self.assertEqual(self.manager.provider_spec.budget.cache_ttl, 300)
        with patch('agent_manager.models.adapters.read', side_effect=AssertionError('native probe')):
            self.manager.budget(cached=True)
            self.manager.models(cwd=self.base, cached=True)

    def test_model_failure_does_not_block_budget_display(self):
        with patch('agent_manager.models.adapters.read', side_effect=ModelError('timeout')):
            result = self.manager.budget(refresh=True)
        self.assertEqual(result['status'], 'ok')

    def test_budget_five_minute_boundary_refreshes_without_refreshing_models(self):
        now = time.time()
        from agent_manager.budgets.models import bucket
        with patch('agent_manager.models.adapters.read', return_value=([model('native')], 'codex-models', '0.164.0')) as models:
            with patch('agent_manager.budgets.adapters.read', return_value=[bucket('native', 'Native', remaining_percent=50)]) as budgets:
                with patch('agent_manager.models.service.time.time', return_value=now): self.manager.budget()
                with patch('agent_manager.models.service.time.time', return_value=now + 299): self.assertTrue(self.manager.budget()['cached'])
                with patch('agent_manager.models.service.time.time', return_value=now + 300): self.assertFalse(self.manager.budget()['cached'])
                self.assertEqual(budgets.call_count, 2)
                self.assertEqual(models.call_count, 0)



class ModelManifestTests(unittest.TestCase):
    def test_fixed_provider_adapter_and_exact_version_bounds(self):
        import tomllib
        from agent_manager.providers.schema import ManifestError, parse_definition
        original = tomllib.loads(Path('agent_manager/providers/definitions/codex.toml').read_text())
        for key, value in (('adapter', 'copilot-models'), ('versions', []), ('versions', ['latest']),
                ('timeout', True), ('cache_ttl', 86401), ('credentials', 'secret')):
            document = json.loads(json.dumps(original))
            document['terminal']['models']['discovery'][key] = value
            with self.subTest(key=key), self.assertRaises(ManifestError) as error:
                parse_definition(document)
            self.assertNotIn('secret', str(error.exception))


@unittest.skipIf(os.name == 'nt', 'POSIX disposable executable fixture')
class ModelInterfaceTests(BudgetFixture, unittest.IsolatedAsyncioTestCase):

    async def test_cli_sdk_and_management_share_canonical_cache(self):
        self.manager.models(cwd=self.base)
        client = Client('codex', config=self.config, root=self.root, runtime=object())
        result = await client.models('work', cwd=self.base, cached=True)
        with redirect_stdout(io.StringIO()) as output:
            execute(['--config', str(self.config), '--home', str(self.root), 'codex', 'models',
                '--profile', 'work', '--cwd', str(self.base), '--cached', '--json'])
        self.assertEqual(json.loads(output.getvalue())['observed_at'], result['observed_at'])
        local = await LocalBackend(config=self.config, home=self.root).execute('get_models',
            {'provider': 'codex', 'profile': 'work', 'cwd': str(self.base), 'cached': True}, 'read')
        self.assertEqual(local['catalog']['observed_at'], result['observed_at'])
        direct = Management(self.manager).execute('get_models', {'profile': 'work', 'cwd': str(self.base), 'cached': True}, request_id='read')
        self.assertEqual(direct['catalog']['observed_at'], result['observed_at'])
        self.assertFalse((self.manager.root / 'profile-operations').exists())

    async def test_validation_and_cached_only_do_not_start_providers(self):
        validate('get_models', {'provider': 'codex', 'cached': True})
        for args in ({'provider': 'codex', 'token': 'secret'}, {'provider': 'codex', 'refresh': True, 'cached': True}):
            with self.assertRaises(Error): validate('get_models', args)
        with patch('agent_manager.models.adapters.read', side_effect=AssertionError('native probe')):
            for manager in self.managers.values():
                self.assertEqual(manager.models(cwd=self.base, cached=True)['status'], 'unknown')
            with self.assertRaises(Error): self.manager.models(cwd=self.base, refresh=True, cached=True)
            with self.assertRaises(Error): self.manager.models('missing', cwd=self.base)
            with self.assertRaises(Error): self.manager.models(cwd=self.base / 'missing')
