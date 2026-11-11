"""Saved argv persistence, native launch ordering and environment lifecycle."""
from contextlib import redirect_stdout
import io
import json
import tomllib
import unittest

from agent_manager import Error, Manager
from agent_manager.environments.operations import revision
from agent_manager.interfaces.cli.dispatch import execute
from agent_manager.profiles.operations import duplicate_profile
from agent_manager.providers.saved_args import configure
from tests.support import ProfileFixture


class SavedArgumentsTests(ProfileFixture, unittest.TestCase):
    def invoke(self, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            execute(['--config', str(self.config), '--home', str(self.base / 'homes'), 'test', 'config', 'args', *args])
        return output.getvalue()

    def reload(self):
        return Manager('test', self.config, self.base / 'homes')

    def test_provider_add_list_remove_clear_preserves_values_and_unrelated_settings(self):
        original = tomllib.loads(self.config.read_text())
        self.config.write_text('# Keep this comment\n' + self.config.read_text())
        self.invoke('add', '--', '--model', 'model with spaces', '--yolo')
        self.assertEqual(self.reload().config['launch_args'], ['--model', 'model with spaces', '--yolo'])
        self.invoke('add', '--', '--label', '$(touch nope); echo test')
        self.assertIn('# Keep this comment', self.config.read_text())
        self.assertEqual(json.loads(self.invoke('list', '--json'))['args'][-1], '$(touch nope); echo test')
        self.invoke('remove', '--', '--model', 'model with spaces')
        self.assertEqual(self.reload().config['launch_args'][0], '--yolo')
        self.invoke('clear')
        result = tomllib.loads(self.config.read_text())
        self.assertEqual(result['providers']['test'].pop('launch_args'), [])
        self.assertEqual(result, original)

    def test_profile_arguments_follow_defaults_and_launch_arguments_come_last(self):
        self.manager.new_profile('work')
        self.invoke('add', '--', '--global', 'one')
        self.invoke('add', '--profile', 'work', '--', '--scoped', 'two words')
        manager = self.reload()
        self.assertEqual(manager._run_native('work', ['--once', 'three words']), 0)
        observed = json.loads((manager.home('work') / 'observed.json').read_text())
        self.assertEqual(observed['args'], ['--configured', '--global', 'one', '--scoped', 'two words', '--once', 'three words'])

    def test_profile_alias_rename_duplicate_and_retirement(self):
        self.manager.new_profile('work')
        self.manager.alias_profile('alias', 'work')
        target = self.manager.resolve_profile('work')
        before = revision(self.manager.state(), target.environment_id)
        configure(self.manager, 'add', ['--yolo'], profile='work')
        self.assertNotEqual(before, revision(self.manager.state(), target.environment_id))
        self.manager.rename('alias', 'renamed')
        self.assertEqual(configure(self.manager, 'list', profile='renamed')['args'], ['--yolo'])
        duplicate_profile(self.manager, 'work', 'copy')
        configure(self.manager, 'clear', profile='copy')
        self.assertEqual(configure(self.manager, 'list', profile='work')['args'], ['--yolo'])
        self.manager.new_profile('retire')
        configure(self.manager, 'add', ['--temp'], profile='retire')
        retired = self.manager.resolve_profile('retire').environment_id
        from agent_manager.profiles.operations import delete_profile
        delete_profile(self.manager, 'retire'); self.manager.forget_environment(retired)
        self.assertNotIn(retired, self.manager.state()['launch_args'])

    def test_remove_matches_complete_sequence_and_preserves_repetitions(self):
        self.invoke('add', '--', '--config', 'a', '--config', 'b', '--config', 'a')
        self.invoke('remove', '--', '--config', 'a')
        self.assertEqual(self.reload().config['launch_args'], ['--config', 'b'])

    def test_multiline_existing_config_is_rewritten_without_losing_settings(self):
        self.config.write_text(self.config.read_text() + 'launch_args = [\n "--old",\n]\n')
        original = tomllib.loads(self.config.read_text())
        self.invoke('add', '--', '--new')
        expected = original; expected['providers']['test']['launch_args'].append('--new')
        self.assertEqual(tomllib.loads(self.config.read_text()), expected)

    def test_provider_alias_table_keeps_unrelated_preferences(self):
        self.config.write_text('[providers."copilot-cli"]\nruntime = "direct"\nlaunch_args = ["--old"]\n')
        manager = Manager('copilot', self.config, self.base / 'homes')
        configure(manager, 'add', ['--new'])
        self.assertEqual(Manager('copilot', self.config, self.base / 'homes').config['launch_args'], ['--old', '--new'])
        self.assertEqual(tomllib.loads(self.config.read_text())['providers']['copilot-cli']['runtime'], 'direct')

    def test_empty_config_and_header_without_newline(self):
        self.config.write_text('[providers.test]')
        self.invoke('add', '--', '--yolo')
        self.assertEqual(self.reload().config['launch_args'], ['--yolo'])
        self.config.write_text('')
        self.invoke('add', '--', '--yolo')
        self.assertEqual(self.reload().config['launch_args'], ['--yolo'])

    def test_missing_values_and_nul_are_rejected_without_writing(self):
        original = self.config.read_bytes()
        for args in [('add',), ('remove',), ('clear', '--', '--yolo'), ('add', '--', 'bad\0value')]:
            with self.subTest(args=args), self.assertRaises(Error): self.invoke(*args)
        self.assertEqual(self.config.read_bytes(), original)

    def test_codex_and_opencode_use_scoped_arguments_with_explicit_model_last(self):
        for provider in ('codex', 'opencode'):
            with self.subTest(provider=provider):
                manager = Manager(provider, self.config, self.base / 'homes')
                manager.new_profile('main')
                configure(manager, 'add', ['--global'])
                configure(manager, 'add', ['--scoped'], profile='main')
                result = manager.launch_args('main', ['--once'], model='openai/example' if provider == 'opencode' else 'example')
                self.assertEqual(result, (['--standalone'] if provider == 'opencode' else []) + [
                    '--global', '--scoped', '--model', 'openai/example' if provider == 'opencode' else 'example', '--once'])

    def test_list_does_not_create_provider_state_or_configuration_locks(self):
        original = self.config.read_bytes()
        self.assertEqual(json.loads(self.invoke('list', '--json'))['args'], [])
        self.assertFalse((self.config.parent / '.locks').exists())
        self.assertFalse(self.manager.root.exists())
        self.assertEqual(self.config.read_bytes(), original)


if __name__ == '__main__': unittest.main()
