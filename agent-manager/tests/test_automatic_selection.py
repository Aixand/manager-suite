"""Automatic startup ranks disposable account observations, never real logins."""
from contextlib import redirect_stderr
from io import StringIO
import json
import os
import threading
import time
import unittest
from unittest.mock import patch

from agent_manager import Error
from agent_manager.budgets.models import BudgetError, bucket
from agent_manager.budgets.ranking import Ineligible, score
from agent_manager.interfaces.cli.dispatch import execute
from agent_manager.interfaces.cli.start import start
from agent_manager.profiles.operations import delete_profile
from agent_manager.profiles.selection import choose, verify
from tests.test_budget_interfaces import BudgetFixture


def windows(primary, secondary=None):
    result = [bucket('codex:primary', 'Short window', remaining_percent=primary,
        resets_at=time.time() + 3600)]
    if secondary is not None:
        result.append(bucket('codex:secondary', 'Long window', remaining_percent=secondary,
            resets_at=time.time() + 86400))
    return result


@unittest.skipIf(os.name == 'nt', 'POSIX disposable native executable fixture')
class AutomaticSelectionTests(BudgetFixture, unittest.TestCase):
    def prepare(self, values, manager=None):
        manager = manager or self.manager
        for name in values:
            if name not in manager.state()['profiles']: manager.new_profile(name)
        homes = {manager.home(name): rows for name, rows in values.items()}
        return self.enterContext(patch('agent_manager.budgets.adapters.read',
            side_effect=lambda m, home, policy: homes[home]))

    def test_highest_limiting_window_wins_without_changing_the_default(self):
        reader = self.prepare({'work': windows(95, 10), 'other': windows(60, 50)})
        default = self.manager.state()['default_profile_id']
        decision = choose(self.manager)
        self.assertEqual(str(decision.target), 'other')
        self.assertEqual(decision.score.remaining_percent, 50)
        self.assertEqual(self.manager.state()['default_profile_id'], default)
        self.assertEqual(reader.call_count, 2)
        verify(self.manager, decision)

    def test_codex_single_view_without_a_limit_id_is_an_applicable_allowance(self):
        for name, used in (('work', 90), ('other', 20)):
            if name == 'other': self.manager.new_profile(name)
            (self.manager.home(name) / 'quota-fixture.json').write_text(json.dumps({
                'rateLimits': {'primary': {'usedPercent': used, 'resetsAt': time.time() + 3600}}}))
        with patch.dict(os.environ, BUDGET_FIXTURE_VERSION='0.162.1'):
            decision = choose(self.manager)
        self.assertEqual(str(decision.target), 'other')
        self.assertEqual(decision.score.remaining_percent, 80)
        self.assertEqual(decision.score.buckets, ('default:primary',))

    def test_aliases_query_once_and_ties_use_default_then_stable_name(self):
        reader = self.prepare({'work': windows(80), 'other': windows(80)})
        self.manager.alias_profile('favorite', 'other'); self.manager.set_default('favorite')
        decision = choose(self.manager)
        self.assertEqual(str(decision.target), 'favorite')
        self.assertEqual(reader.call_count, 2)
        self.manager.rename('favorite', 'renamed')
        self.assertEqual(str(choose(self.manager).target), 'renamed')
        state = self.manager.state(); state['default_profile_id'] = None; self.manager.save(state)
        self.assertEqual(str(choose(self.manager).target), 'other')
        self.assertEqual(reader.call_count, 2)

    def test_budget_selection_needs_no_default_and_ignores_unnamed_environments(self):
        reader = self.prepare({'work': windows(60), 'other': windows(90)})
        self.manager.new_profile('unnamed'); delete_profile(self.manager, 'unnamed')
        state = self.manager.state(); state['default_profile_id'] = None; self.manager.save(state)
        self.assertEqual(str(choose(self.manager).target), 'other')
        self.assertEqual(reader.call_count, 2)

    def test_empty_registry_has_an_actionable_error(self):
        delete_profile(self.manager, 'work')
        with self.assertRaisesRegex(Error, 'Create or connect'):
            choose(self.manager)

    def test_no_fallback_to_a_default_with_exhausted_unknown_or_expired_allowance(self):
        for rows in (windows(0), windows(None), [bucket('codex:primary', 'Old', remaining_percent=90, resets_at=1)]):
            with self.subTest(rows=rows), patch('agent_manager.budgets.adapters.read', return_value=rows), patch.object(self.manager, '_run_native') as run:
                self.manager.budget(refresh=True)
                with self.assertRaisesRegex(Error, 'No codex profile has fresh usable allowance'):
                    start(self.manager, [])
                run.assert_not_called()

    def test_failed_refresh_and_unavailable_accounts_are_excluded(self):
        self.prepare({'work': windows(95), 'other': windows(50)})
        self.manager.budget('work')
        with patch('agent_manager.budgets.adapters.read', side_effect=BudgetError('timeout')):
            self.manager.budget('work', refresh=True)
        # The previously higher reading remains visibly stale after its failed refresh.
        self.assertEqual(str(choose(self.manager).target), 'other')

    def test_inaccessible_candidate_is_excluded_without_losing_a_usable_candidate(self):
        self.prepare({'work': windows(95), 'other': windows(50)})
        self.manager.budgets()
        original = self.manager.home
        def home(target):
            if str(target) == 'work': raise OSError('private fixture path')
            return original(target)
        with patch.object(self.manager, 'home', side_effect=home):
            self.assertEqual(str(choose(self.manager).target), 'other')

    def test_optional_paid_balances_and_unrelated_meters_cannot_override_base_allowance(self):
        self.prepare({'work': windows(0) + [bucket('codex:credits', 'Credits', unit='credits', unlimited=True)],
            'other': windows(40) + [bucket('review:primary', 'Review', remaining_percent=None, resets_at=1),
                bucket('spend', 'Cap', kind='spending-cap', unit='usd', remaining=0)]})
        self.assertEqual(str(choose(self.manager).target), 'other')

    def test_copilot_unlimited_allowance_beats_a_finite_full_entitlement(self):
        manager = self.managers['copilot']
        self.prepare({'work': [bucket('premium_interactions', 'Premium', unit='requests', remaining_percent=100)],
            'other': [bucket('premium_interactions', 'Premium', unit='requests', unlimited=True)]}, manager)
        decision = choose(manager)
        self.assertEqual(str(decision.target), 'other')
        self.assertTrue(decision.score.unlimited)
        self.assertIsNone(decision.score.remaining_percent)

    def test_counts_need_a_known_capacity_and_zero_entitlement_is_not_unlimited(self):
        self.prepare({'work': [bucket('codex:primary', 'Window', unit='requests', remaining=90)],
            'other': [bucket('codex:primary', 'Window', unit='requests', remaining=40, limit=80)]})
        self.assertEqual(choose(self.manager).score.remaining_percent, 50)

    def test_claude_model_scopes_follow_explicit_forwarded_and_saved_models(self):
        manager = self.managers['claude']
        def rows(global_left, opus_left, sonnet_left):
            return [bucket('claude:session', 'Session', remaining_percent=global_left),
                bucket('claude:weekly_scoped:opus', 'Opus', remaining_percent=opus_left, scope={'model': 'Opus'}),
                bucket('claude:weekly_scoped:sonnet', 'Sonnet', remaining_percent=sonnet_left, scope={'model': 'Sonnet'})]
        self.prepare({'work': rows(90, 0, 80), 'other': rows(60, 60, 60)}, manager)
        self.assertEqual(str(choose(manager, model='sonnet').target), 'work')
        self.assertEqual(str(choose(manager, model='opus').target), 'other')
        self.assertEqual(str(choose(manager, model='opusplan').target), 'other')
        self.assertEqual(str(choose(manager, model='unknown-model').target), 'other')
        for arguments in (['--model', 'claude-sonnet-4-7'], ['--model=sonnet'], ['-m', 'sonnet']):
            self.assertEqual(str(choose(manager, args=arguments).target), 'work')
        self.assertEqual(str(choose(manager, args=['--', '--model', 'sonnet']).target), 'other')
        manager.set_model('work', 'sonnet')
        self.assertEqual(str(choose(manager).target), 'work')

    def test_antigravity_shared_environment_uses_the_applicable_model_group(self):
        manager = self.managers['antigravity']
        reader = self.prepare({'work': [
            bucket('gemini-weekly', 'Gemini', remaining_percent=70, scope={'model_group': 'Gemini Models'}),
            bucket('3p-weekly', 'Other', remaining_percent=0, scope={'model_group': 'Claude and GPT models'})]}, manager)
        manager.alias_profile('another-name', 'work')
        self.assertEqual(choose(manager, model='gemini-pro').score.remaining_percent, 70)
        self.assertEqual(reader.call_count, 1)
        with self.assertRaises(Error): choose(manager, model='claude-opus')

    def test_model_and_quota_changes_are_rejected_before_launch(self):
        self.prepare({'work': windows(60)})
        decision = choose(self.manager)
        state = self.manager.state(); key = decision.target.environment_id
        state['models'] = {key: 'new-model'}; self.manager.save(state)
        with self.assertRaisesRegex(Error, 'environment changed'): verify(self.manager, decision)
        decision = choose(self.manager)
        self.manager.budget('work', refresh=True)
        with self.assertRaisesRegex(Error, 'budget changed or expired'): verify(self.manager, decision)

    def test_expired_observation_is_rechecked_at_launch(self):
        self.prepare({'work': windows(60)})
        decision = choose(self.manager)
        with patch('agent_manager.budgets.ranking.time.time', return_value=decision.budget['observed_at'] + 301):
            with self.assertRaisesRegex(Error, 'budget changed or expired'): verify(self.manager, decision)

    def test_rename_and_name_reuse_during_inspection_do_not_redirect_the_target(self):
        original = self.manager.resolve_profile('work')
        def read(manager, home, policy):
            manager.rename('work', 'renamed')
            manager.new_profile('work')
            return windows(60)
        with patch('agent_manager.budgets.adapters.read', side_effect=read):
            decision = choose(self.manager)
        self.assertEqual(str(decision.target), 'renamed')
        self.assertEqual(decision.target.environment_id, original.environment_id)
        self.assertNotEqual(decision.target.environment_id, self.manager.resolve_profile('work').environment_id)
        verify(self.manager, decision)

    def test_parallel_native_reads_are_bounded(self):
        for index in range(5): self.manager.new_profile('other' + str(index))
        active, peak, lock = 0, 0, threading.Lock()
        def read(manager, home, policy):
            nonlocal active, peak
            with lock:
                active += 1; peak = max(peak, active)
            time.sleep(.05)
            with lock: active -= 1
            return windows(60)
        with patch('agent_manager.budgets.adapters.read', side_effect=read): choose(self.manager)
        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, 4)

    def test_opencode_keeps_its_explicit_default_without_querying_budgets(self):
        manager = self.managers['opencode']
        with patch.object(manager, 'budget') as query:
            self.assertEqual(str(choose(manager).target), 'work')
        query.assert_not_called()

    def test_real_cli_path_selects_and_launches_only_the_chosen_disposable_home(self):
        self.manager.new_profile('other')
        for name, used in (('work', 90), ('other', 20)):
            (self.manager.home(name) / 'quota-fixture.json').write_text(json.dumps({
                'rateLimitsByLimitId': {'codex': {'primary': {'usedPercent': used,
                    'windowDurationMins': 300, 'resetsAt': time.time() + 3600}}}}))
        before = self.manager.state()['default_profile_id']
        arguments = ['--config', str(self.config), '--home', str(self.root), 'codex']
        with redirect_stderr(StringIO()) as output:
            self.assertEqual(execute([*arguments, 'start', '--model', 'fixture-model', '--', 'offline launch']), 0)
        self.assertIn('selected codex profile other', output.getvalue())
        launched = json.loads((self.manager.home('other') / 'launched.json').read_text())
        self.assertEqual(launched['home'], str(self.manager.home('other')))
        self.assertIn('fixture-model', launched['args'])
        self.assertIn('offline launch', launched['args'])
        self.assertFalse((self.manager.home('work') / 'launched.json').exists())
        self.assertEqual(self.manager.state()['default_profile_id'], before)
        with patch('agent_manager.manager.Manager.budget', side_effect=AssertionError('Explicit run must not inspect budgets')):
            self.assertEqual(execute([*arguments, 'run', 'work']), 0)

    def test_cli_installs_final_validation_without_silently_reselecting(self):
        self.prepare({'work': windows(60)})
        def invoke(target, arguments, **options):
            self.manager.budget(target, refresh=True)
            options['validate']()
        with patch.object(self.manager, '_run_native', side_effect=invoke), redirect_stderr(StringIO()):
            with self.assertRaisesRegex(Error, 'budget changed or expired'): start(self.manager, [])


    def test_eligible_environment_filter_excludes_higher_budget_profiles(self):
        self.prepare({'work': windows(20), 'other': windows(99)})
        identifier = self.manager.resolve_profile('work').environment_id
        selected = choose(self.manager, environment_ids={identifier})
        self.assertEqual(str(selected.target), 'work')
        self.assertEqual(selected.score.remaining_percent, 20)


if __name__ == '__main__': unittest.main()
