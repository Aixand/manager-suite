"""Session-first continuation with advisory allowance and stable native targets."""
from dataclasses import replace
import os
import time
import unittest
import uuid
from unittest.mock import Mock, patch

from agent_manager import Error
from agent_manager.budgets.models import BudgetError, bucket
from agent_manager.interfaces.cli.continuation import continue_session
from agent_manager.interfaces.cli.dispatch import execute
from agent_manager.interfaces.cli.profile_picker import choices
from agent_manager.profiles.sessions import remember
from tests.test_budget_interfaces import BudgetFixture


def windows(short, long=None):
    rows = [bucket('codex:primary', 'Short window', remaining_percent=short,
        resets_at=time.time() + 3600)]
    if long is not None:
        rows.append(bucket('codex:secondary', 'Long window', remaining_percent=long,
            resets_at=time.time() + 86400))
    return rows


@unittest.skipIf(os.name == 'nt', 'POSIX disposable native executable fixture')
class ContinueBudgetTests(BudgetFixture, unittest.TestCase):
    def setUp(self):
        # Launches are mocked here; picker eligibility must not depend on the
        # host having bubblewrap for a real cross-environment handoff.
        self.enterContext(patch('agent_manager.runtime.native.codex_continuation.available', return_value=True))
        super().setUp()

    def prepare(self, values, manager=None):
        manager = manager or self.manager
        for name in values:
            if name not in manager.state()['profiles']: manager.new_profile(name)
        homes = {manager.home(name): rows for name, rows in values.items()}
        def read(current, home, policy):
            value = homes[home]
            if isinstance(value, Exception): raise value
            return value
        return self.enterContext(patch('agent_manager.budgets.adapters.read', side_effect=read))

    def session(self, manager=None):
        manager = manager or self.manager
        saved = remember(manager, 'work', str(uuid.uuid4()), cwd=self.base, title='Saved conversation')
        self.enterContext(patch('agent_manager.profiles.sessions.listing', return_value=[saved]))
        return saved

    def launch(self):
        return self.enterContext(patch('agent_manager.profiles.sessions.continue_session', return_value=23))

    def test_session_then_sorted_profiles_and_manual_exhausted_choice(self):
        reader = self.prepare({'work': windows(95, 10), 'backup': windows(60, 50), 'empty': windows(0)})
        saved = self.session(); launch = self.launch()
        default = self.manager.state()['default_profile_id']
        prompts = []
        def picker(title, columns, rows, detail):
            prompts.append(title)
            launch.assert_not_called()
            if len(prompts) == 1:
                reader.assert_not_called()
                return 0
            self.assertEqual(columns, ['Profile', 'Budget', 'Native home'])
            self.assertEqual([row[:2] for row in rows], [('backup', '50%'), ('work', '10%'), ('empty', '0% — exhausted')])
            self.assertIn('all rows selectable', detail)
            return 2
        self.assertEqual(continue_session(self.manager, picker=picker), 23)
        self.assertEqual(prompts, ['Choose a native session', 'Choose an execution profile'])
        self.assertEqual(launch.call_args.args, (self.manager, saved['ref']))
        target = launch.call_args.kwargs['profile']
        self.assertEqual(target.environment_id, self.manager.resolve_profile('empty').environment_id)
        launch.call_args.kwargs['validate']()
        self.assertEqual(self.manager.state()['default_profile_id'], default)

    def test_explicit_cli_session_asks_only_for_a_profile_and_forwards_options(self):
        self.prepare({'work': windows(20), 'backup': windows(80)})
        saved = self.session(); launch = self.launch()
        picker = Mock(return_value=0)
        with patch('agent_manager.interfaces.cli.tui.pick', picker), \
                patch('agent_manager.interfaces.cli.tui.require_terminal'):
            result = execute(['--config', str(self.config), '--home', str(self.root), 'codex',
                'continue', saved['native_id'], '--model', 'fixture-model'])
        self.assertEqual(result, 23)
        picker.assert_called_once()
        self.assertEqual(picker.call_args.args[0], 'Choose an execution profile')
        self.assertEqual(launch.call_args.args[1], saved['native_id'])
        self.assertEqual(launch.call_args.kwargs['profile'].environment_id,
            self.manager.resolve_profile('backup').environment_id)
        self.assertEqual(launch.call_args.kwargs['model'], 'fixture-model')

    def test_explicit_profile_bypasses_budgets_and_the_profile_picker(self):
        saved = self.session(); launch = self.launch()
        with patch.object(self.manager, 'budget', side_effect=AssertionError('No quota reads')):
            picker = Mock(side_effect=AssertionError('No picker'))
            self.assertEqual(continue_session(self.manager, session=saved['ref'], profile='work', picker=picker), 23)
            picker.assert_not_called()
            picker = Mock(return_value=0)
            self.assertEqual(continue_session(self.manager, profile='work', picker=picker), 23)
            picker.assert_called_once()
        self.assertEqual(launch.call_count, 2)

    def test_cancellation_at_either_screen_never_launches(self):
        reader = self.prepare({'work': windows(60)})
        self.session(); launch = self.launch()
        self.assertEqual(continue_session(self.manager, picker=Mock(return_value=None)), 0)
        reader.assert_not_called()
        self.assertEqual(continue_session(self.manager, picker=Mock(side_effect=[0, None])), 0)
        launch.assert_not_called()

    def test_aliases_share_queries_and_ties_prefer_default_then_name(self):
        reader = self.prepare({'work': windows(80), 'backup': windows(80)})
        self.manager.alias_profile('favorite', 'backup'); self.manager.set_default('favorite')
        available = choices(self.manager, list(self.manager.state()['profiles']))
        self.assertEqual([str(row.target) for row in available], ['favorite', 'backup', 'work'])
        self.assertEqual([row.budget for row in available], ['80%'] * 3)
        self.assertEqual(reader.call_count, 2)

    def test_fresh_exhausted_unknown_stale_and_failed_readings_are_distinct(self):
        self.prepare({'work': windows(70), 'empty': windows(0), 'unknown': windows(None),
            'old': windows(95), 'disabled': [bucket('codex:primary', 'Window', disabled=True)],
            'reset': [bucket('codex:primary', 'Window', remaining_percent=95, resets_at=1)],
            'signed-out': BudgetError('authentication'), 'unsupported': BudgetError('unsupported'),
            'unreadable': OSError('private fixture path')})
        self.manager.budget('old')
        with patch('agent_manager.budgets.adapters.read', side_effect=BudgetError('timeout')):
            self.manager.budget('old', refresh=True)
        available = choices(self.manager, list(self.manager.state()['profiles']))
        self.assertEqual([str(row.target) for row in available[:2]], ['work', 'empty'])
        labels = {str(row.target): row.budget for row in available}
        self.assertEqual(labels, {'work': '70%', 'empty': '0% — exhausted', 'unknown': 'Unknown',
            'old': 'Stale', 'reset': 'Stale', 'disabled': 'Disabled', 'signed-out': 'Unavailable',
            'unsupported': 'Unsupported CLI', 'unreadable': 'Unavailable'})

    def test_unlimited_sorts_above_full_finite_allowance(self):
        manager = self.managers['copilot']
        self.prepare({'work': [bucket('premium_interactions', 'Requests', remaining_percent=100)],
            'backup': [bucket('premium_interactions', 'Requests', unlimited=True)]}, manager)
        available = choices(manager, ['work', 'backup'])
        self.assertEqual([(str(row.target), row.budget) for row in available], [('backup', 'Unlimited'), ('work', '100%')])

    def test_model_scoping_uses_known_launch_model_and_is_conservative_otherwise(self):
        manager = self.managers['claude']
        def scoped(global_left, opus_left, sonnet_left):
            return [bucket('claude:session', 'Session', remaining_percent=global_left),
                bucket('claude:weekly_scoped:opus', 'Opus', remaining_percent=opus_left, scope={'model': 'Opus'}),
                bucket('claude:weekly_scoped:sonnet', 'Sonnet', remaining_percent=sonnet_left, scope={'model': 'Sonnet'})]
        self.prepare({'work': scoped(90, 0, 80), 'backup': scoped(60, 60, 60)}, manager)
        self.assertEqual(str(choices(manager, ['work', 'backup'])[0].target), 'backup')
        self.assertEqual(str(choices(manager, ['work', 'backup'], model='sonnet')[0].target), 'work')
        self.assertEqual(str(choices(manager, ['work', 'backup'], model='opusplan')[0].target), 'backup')
        manager.set_model('work', 'sonnet')
        self.assertEqual(str(choices(manager, ['work', 'backup'])[0].target), 'work')

    def test_only_compatible_execution_environments_are_queried(self):
        reader = self.prepare({'work': windows(20), 'backup': windows(100)})
        self.session(); self.launch()
        self.manager.provider_spec = replace(self.manager.provider_spec,
            sessions=replace(self.manager.provider_spec.sessions, cross_environment_resume=False))
        picker = Mock(side_effect=[0, 0])
        continue_session(self.manager, picker=picker)
        self.assertEqual([row[0] for row in picker.call_args.args[2]], ['work'])
        self.assertEqual(reader.call_count, 1)

    def test_alias_rename_and_name_reuse_while_picking_do_not_redirect_resume(self):
        self.prepare({'work': windows(60)})
        saved = self.session(); launch = self.launch()
        target = self.manager.resolve_profile('work')
        def picker(title, columns, rows, detail):
            self.manager.rename('work', 'renamed')
            self.manager.new_profile('work')
            return 0
        continue_session(self.manager, session=saved['ref'], picker=picker)
        self.assertEqual(launch.call_args.kwargs['profile'].environment_id, target.environment_id)
        self.assertEqual(launch.call_args.kwargs['profile'].profile_id, target.profile_id)
        launch.call_args.kwargs['validate']()

    def test_changed_environment_preferences_require_a_new_selection(self):
        self.prepare({'work': windows(60)})
        saved = self.session(); launch = self.launch()
        def picker(*_):
            state = self.manager.state()
            state['models'] = {self.manager.resolve_profile('work').environment_id: 'different-model'}
            self.manager.save(state)
            return 0
        with self.assertRaisesRegex(Error, 'environment settings changed'):
            continue_session(self.manager, session=saved['ref'], picker=picker)
        launch.assert_not_called()

    def test_final_launch_guard_checks_settings_but_does_not_require_budget(self):
        self.prepare({'work': windows(60)})
        saved = self.session(); launch = self.launch()
        continue_session(self.manager, session=saved['ref'], picker=Mock(return_value=0))
        self.cache().unlink()
        launch.call_args.kwargs['validate']()
        state = self.manager.state()
        state['models'] = {self.manager.resolve_profile('work').environment_id: 'different-model'}
        self.manager.save(state)
        with self.assertRaisesRegex(Error, 'environment settings changed'):
            launch.call_args.kwargs['validate']()

    def test_no_discovered_sessions_uses_budget_sorted_native_picker_fallback(self):
        self.prepare({'work': windows(20), 'backup': windows(80)})
        picker = Mock(return_value=0)
        with patch('agent_manager.profiles.sessions.listing', return_value=[]), patch.object(self.manager, '_run_native', return_value=31) as run:
            self.assertEqual(continue_session(self.manager, picker=picker), 31)
        picker.assert_called_once()
        self.assertEqual(picker.call_args.args[0], 'Choose a profile')
        self.assertEqual(picker.call_args.args[2][0][:2], ('backup', '80%'))
        self.assertEqual(run.call_args.args[0].environment_id, self.manager.resolve_profile('backup').environment_id)
        self.assertEqual(run.call_args.args[1], ['resume'])

    def test_opencode_is_na_without_budget_probes(self):
        manager = self.managers['opencode']
        manager.new_profile('backup')
        with patch.object(manager, 'budget', side_effect=AssertionError('No OpenCode quota reads')):
            available = choices(manager, ['backup', 'work'])
        self.assertEqual([(str(row.target), row.budget) for row in available], [('work', 'N/A'), ('backup', 'N/A')])

    def test_antigravity_refuses_managed_resume_before_picker_or_budget_queries(self):
        manager = self.managers['antigravity']; picker = Mock()
        with patch.object(manager, 'budget') as read:
            with self.assertRaisesRegex(Error, 'Exact retained-session continuation is unavailable'):
                continue_session(manager, picker=picker)
        read.assert_not_called(); picker.assert_not_called()

    def test_unknown_session_fails_before_querying_budgets(self):
        self.session(); launch = self.launch(); picker = Mock()
        with patch.object(self.manager, 'budget') as read, self.assertRaisesRegex(Error, 'Session was not found'):
            continue_session(self.manager, session='missing', picker=picker)
        read.assert_not_called(); launch.assert_not_called(); picker.assert_not_called()

    def test_noninteractive_continuation_requires_explicit_profile_before_any_queries(self):
        saved = self.session(); launch = self.launch()
        with patch('agent_manager.interfaces.cli.tui.sys.stdin.isatty', return_value=False), \
                patch.object(self.manager, 'budget') as read:
            with self.assertRaisesRegex(Error, 'interactive terminal'):
                continue_session(self.manager, session=saved['ref'])
        read.assert_not_called(); launch.assert_not_called()


if __name__ == '__main__': unittest.main()
