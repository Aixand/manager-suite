"""Production OAuth quota routing, comparison, cache guards and public interfaces."""
from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import shutil
import time
import unittest
from unittest.mock import patch

from agent_manager import Error
from agent_manager.budgets.service import matches_observation
from agent_manager.integrations import readers
from agent_manager.interfaces.cli.dispatch import execute
from agent_manager.interfaces.cli.profile_picker import choices
from agent_manager.models.schema import ModelError
from agent_manager.profiles.selection import choose, verify
from tests.test_integrations import IntegrationFixture, bridge_fixture, native_fixture
from tests.manual import opencode_openai_bridge as manual


def allowance(left=55, secondary=None, reset=None, limit_id='codex'):
    windows = [dict(limit_id=limit_id, window='primary', used_percent=100-left,
        remaining_percent=left, window_minutes=10080, resets_at=reset or time.time() + 3600)]
    if secondary is not None:
        windows.append(dict(limit_id=limit_id, window='secondary', used_percent=100-secondary,
            remaining_percent=secondary, window_minutes=300, resets_at=time.time() + 1800))
    return dict(status='ok', reason='chatgpt-codex-allowance', observation=dict(windows=windows))


@unittest.skipIf(os.name == 'nt', 'POSIX disposable executable fixture')
class OpenCodeOAuthBudgetTests(IntegrationFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.bridge['default_model'] = 'openai/native'
        self.bridge['integrations'][0]['budget'] = allowance()

    def environments(self, values, defaults=None):
        for name in values:
            if name not in self.manager.state()['profiles']: self.manager.new_profile(name)
        data = {}
        for name, value in values.items():
            bridge = copy.deepcopy(self.bridge)
            bridge['integrations'][0]['budget'] = value
            if defaults and name in defaults: bridge['default_model'] = defaults[name]
            data[self.manager.home(name)] = bridge
        self.probe.side_effect = lambda manager, home, cwd, policy: (copy.deepcopy(self.native), copy.deepcopy(data[home]), '2.0.18')
        return data

    def test_verified_weekly_primary_keeps_its_real_duration_and_scope(self):
        result = self.manager.budget(cwd=self.base)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['selected_route']['auth_method'], 'oauth')
        row = result['buckets'][0]
        self.assertEqual(row['id'], 'openai:codex:primary')
        self.assertEqual(row['remaining_percent'], 55)
        self.assertEqual(row['window_seconds'], 604800)
        self.assertEqual(row['scope'], dict(surface='codex', limit_id='codex'))
        self.assertNotIn('fixture-secret', json.dumps(result))

    def test_aliases_share_the_five_minute_read(self):
        self.manager.alias_profile('alias', 'work')
        first = self.manager.budget('work', cwd=self.base)
        second = self.manager.budget('alias', cwd=self.base)
        self.assertEqual(first['observed_at'], second['observed_at'])
        self.assertTrue(second['cached'])
        self.assertEqual(self.probe.call_count, 1)

    def test_reset_before_cache_expiry_marks_cached_data_stale_and_refreshes(self):
        now = time.time()
        self.bridge['integrations'][0]['budget'] = allowance(reset=now + 10)
        with patch('time.time', return_value=now): self.manager.budget(cwd=self.base)
        with patch('time.time', return_value=now + 20):
            old = self.manager.budget(cwd=self.base, cached=True)
            self.assertEqual(old['status'], 'stale')
            self.assertEqual(old['buckets'][0]['status'], 'stale')
            self.bridge['integrations'][0]['budget'] = allowance(reset=now + 100)
            self.assertEqual(self.manager.budget(cwd=self.base)['status'], 'ok')
        self.assertEqual(self.probe.call_count, 2)

    def test_codex_dependency_replacement_invalidates_the_connection_cache(self):
        self.manager.budget(cwd=self.base)
        old = self.managers['codex'].config['executable']
        replacement = self.base / 'other-codex'; shutil.copyfile(old, replacement); replacement.chmod(0o700)
        self.config.write_text(self.config.read_text().replace(json.dumps(old), json.dumps(str(replacement))))
        self.assertEqual(self.manager.budget(cwd=self.base, cached=True)['status'], 'unknown')
        self.assertEqual(self.manager.budget(cwd=self.base)['status'], 'ok')
        self.assertEqual(self.probe.call_count, 2)

    def test_start_ranks_the_most_restrictive_window_and_shares_alias_queries(self):
        self.environments({'work': allowance(95, 10), 'backup': allowance(60, 50)})
        self.manager.alias_profile('short', 'backup')
        before = self.manager.state()['default_profile_id']
        decision = choose(self.manager)
        self.assertEqual(str(decision.target), 'backup')
        self.assertEqual(decision.score.remaining_percent, 50)
        self.assertEqual(decision.model, 'openai/native')
        self.assertEqual(self.probe.call_count, 2)
        self.assertEqual(self.manager.state()['default_profile_id'], before)
        verify(self.manager, decision)

    def test_forwarded_model_controls_the_budget_route(self):
        self.environments({'work': allowance(10), 'backup': allowance(80)},
            {'work': 'openrouter/openai/native', 'backup': 'openrouter/openai/native'})
        decision = choose(self.manager, args=['--model=openai/native'])
        self.assertEqual(str(decision.target), 'backup')
        self.assertEqual(decision.budget['selected_model'], 'openai/native')

    def test_start_launches_the_discovered_model_without_overriding_a_forwarded_model(self):
        from agent_manager.interfaces.cli.start import start
        self.environments({'work': allowance()})
        with patch.object(self.manager, '_run_native', return_value=0) as run:
            start(self.manager, [])
            self.assertEqual(run.call_args.kwargs['model'], 'openai/native')
            run.call_args.kwargs['validate']()
            start(self.manager, ['--model=openai/native'])
            self.assertNotIn('model', run.call_args.kwargs)

    def test_mixed_services_or_models_keep_the_default_without_cross_ranking(self):
        for other in ('openrouter/openai/native', 'openai/other-model'):
            with self.subTest(other=other):
                self.environments({'work': allowance(10), 'backup': allowance(90)}, {'backup': other})
                for target in ('work', 'backup'): self.manager.integrations(target, refresh=True)
                decision = choose(self.manager)
                self.assertEqual(str(decision.target), 'work')
                self.assertIsNone(decision.score)
                self.assertIn('not comparable', decision.reason)

    def test_unknown_allowance_is_excluded_and_all_unknown_uses_the_default(self):
        unknown = dict(status='unavailable', reason='missing-cli')
        data = self.environments({'work': unknown, 'backup': allowance(40)})
        self.assertEqual(str(choose(self.manager).target), 'backup')
        data[self.manager.home('backup')]['integrations'][0]['budget'] = unknown
        self.manager.integrations('backup', refresh=True)
        decision = choose(self.manager)
        self.assertEqual(str(decision.target), 'work')
        self.assertIsNone(decision.score)

    def test_exhausted_profiles_are_not_silently_launched_by_default_fallback(self):
        self.environments({'work': allowance(0), 'backup': allowance(0)})
        with self.assertRaisesRegex(Error, 'fresh usable allowance'): choose(self.manager)

    def test_unfamiliar_meter_is_visible_but_not_qualified_for_ranking(self):
        self.environments({'work': allowance(90, limit_id='codex_other')})
        decision = choose(self.manager)
        self.assertIsNone(decision.score)
        self.assertEqual(decision.target, 'work')

    def test_final_guard_rejects_refresh_or_dependency_change_without_native_io(self):
        self.environments({'work': allowance()})
        decision = choose(self.manager)
        with patch.object(self.manager, 'integrations', side_effect=AssertionError('Guard must be read-only')):
            verify(self.manager, decision)
        self.manager.integrations('work', refresh=True)
        self.assertFalse(matches_observation(self.manager, decision.target, decision.budget))
        with self.assertRaisesRegex(Error, 'budget changed'): verify(self.manager, decision)

    def test_continue_picker_sorts_comparable_connections_and_labels_the_service(self):
        self.environments({'work': allowance(20), 'backup': allowance(80)})
        rows = choices(self.manager, ['work', 'backup'])
        self.assertEqual([(str(row.target), row.budget) for row in rows], [('backup', '80% · OpenAI'), ('work', '20% · OpenAI')])
        self.assertTrue(all(row.comparable for row in rows))

    def test_continue_picker_uses_default_order_for_mixed_services(self):
        self.environments({'work': allowance(20), 'backup': allowance(80)}, {'work': 'openrouter/openai/native'})
        rows = choices(self.manager, ['work', 'backup'])
        self.assertEqual(str(rows[0].target), 'work')
        self.assertFalse(any(row.comparable for row in rows))

    def test_normal_cli_budget_path_emits_the_shared_oauth_observation(self):
        with redirect_stdout(io.StringIO()) as output:
            execute(['--config', str(self.config), '--home', str(self.root), 'opencode', 'budget',
                'work', '--cwd', str(self.base), '--model', 'openai/native', '--refresh', '--json'])
        result = json.loads(output.getvalue())
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['buckets'][0]['remaining_percent'], 55)

    def test_invalid_native_windows_and_missing_observations_do_not_qualify(self):
        for mutate in (lambda row: row['observation']['windows'][0].update(remaining_percent=100),
                lambda row: row['observation']['windows'][0].update(window='unknown'),
                lambda row: row.pop('observation')):
            value = allowance(); mutate(value)
            with self.subTest(value=value), self.assertRaises(ModelError): readers.budget('openai-native', value, observed_at=time.time())

    def test_production_manual_check_exercises_alias_cli_sdk_and_mcp_using_only_disposable_state(self):
        base = self.base / 'manual'; base.mkdir()
        manager, variables, _, _ = manual.setup(base, Path(self.manager.config['executable']),
            Path(self.managers['codex'].config['executable']), False)
        result = manual.production(base, variables)
        self.assertEqual(result['status'], 'production-verified')
        self.assertTrue(result['allowance_scope_verified'])
        self.assertEqual(result['model_calls'], 0)
        self.assertTrue(all(result['checks'].values()))
        self.assertEqual(result['budget']['buckets'][0]['remaining_percent'], 55)
