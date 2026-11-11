"""Version-three manifest validation and native-profile preference resolution."""
from pathlib import Path
import tempfile
import tomllib
import unittest

from agent_manager import Error
from agent_manager.providers import ManifestError, load_definitions
from agent_manager.providers.config import load, load_registry
from agent_manager.providers.loader import DEFINITIONS
from agent_manager.providers.schema import parse_definition
from agent_manager.providers.strategies import manager_config
from tests.support import native_manifest


def document(provider):
    return tomllib.loads((DEFINITIONS / (provider + '.toml')).read_text())


class ManifestTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.base = Path(folder.name)
        self.config = self.base / 'providers.toml'; self.config.write_text('')
        self.definitions = self.base / 'providers'; self.definitions.mkdir()

    def local(self, provider, source):
        (self.definitions / (provider + '.toml')).write_text(source)

    def test_new_provider_is_resolved_without_registration_code(self):
        self.local('example', native_manifest('example', ('ex',), environment='EXAMPLE_HOME'))
        policy = load_registry(self.config).resolve('ex')
        self.assertEqual(policy.id, 'example')
        self.assertEqual(policy.home.environment, 'EXAMPLE_HOME')
        self.assertEqual(policy.login.args, ('login',))
        self.assertEqual(policy.sessions.resume_args, ('--resume',))
        self.assertTrue(policy.sessions.retained_resume)
        self.assertFalse(policy.sessions.cross_environment_resume)
        self.assertNotIn('auth_mode', policy.settings)

    def test_duplicate_ids_aliases_and_alias_id_collisions_are_rejected(self):
        for first, aliases, second, others, message in (
            ('one', (), 'one', (), 'duplicate provider ID'),
            ('one', ('shared',), 'two', ('shared',), 'duplicate provider name or alias'),
            ('one', ('two',), 'two', (), 'duplicate provider name or alias')):
            with self.subTest(message=message), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / 'a.toml').write_text(native_manifest(first, aliases))
                (root / 'b.toml').write_text(native_manifest(second, others))
                with self.assertRaisesRegex(ManifestError, message): load_definitions(root)

    def test_invalid_files_are_visible_and_diagnostics_hide_source_values(self):
        with self.assertRaisesRegex(ManifestError, 'no provider manifests'):
            load_definitions(self.definitions)
        self.local('broken', 'id = "private-invalid-value\n')
        with self.assertRaisesRegex(Error, 'broken.toml: invalid TOML') as caught:
            load(self.config)
        self.assertNotIn('private-invalid-value', str(caught.exception))

    def test_unknown_versions_fields_and_strategies_are_rejected(self):
        cases = [(('schema_version',), version) for version in (True, 1, 2, 4)] + [
            (('terminal', 'launch_arg'), []),
            (('terminal', 'home', 'strategy'), 'python:private.module'),
            (('terminal', 'sessions', 'strategy'), 'custom-code'),
            (('terminal', 'sessions', 'cross_environment_resume'), 'supported')]
        for keys, replacement in cases:
            with self.subTest(keys=keys, replacement=replacement):
                value = document('claude'); target = value
                for key in keys[:-1]: target = target[key]
                target[keys[-1]] = replacement
                with self.assertRaises(ManifestError): parse_definition(value)

    def test_authentication_tables_and_credential_file_declarations_are_rejected(self):
        for provider in ('codex', 'claude', 'copilot', 'antigravity'):
            value = document(provider)
            value['terminal']['auth'] = {'files': ['private-credential-path']}
            with self.subTest(provider=provider), self.assertRaisesRegex(ManifestError, 'unknown field') as caught:
                parse_definition(value)
            self.assertNotIn('private-credential-path', str(caught.exception))

    def test_scopes_environment_and_strategy_must_agree(self):
        for path, replacement in ((('binding',), 'shared'), (('settings_scope',), 'shared'),
                                  (('environment',), 'INVALID-ENV')):
            value = document('claude'); value['terminal']['home'][path[0]] = replacement
            with self.subTest(path=path), self.assertRaises(ManifestError): parse_definition(value)
        value = document('antigravity'); value['terminal']['home']['environment'] = 'FAKE_HOME'
        with self.assertRaisesRegex(ManifestError, 'cannot set an environment override'): parse_definition(value)

    def test_native_arguments_are_literal_and_nul_is_rejected(self):
        value = document('claude')
        value['terminal']['launch_args'] = ['--name', 'with spaces', 'line one\nline two']
        self.assertEqual(manager_config(parse_definition(value))['launch_args'], value['terminal']['launch_args'])
        value['terminal']['launch_args'] = ['private\0value']
        with self.assertRaisesRegex(ManifestError, 'without NUL') as caught: parse_definition(value)
        self.assertNotIn('private', str(caught.exception))

    def test_loaded_definitions_and_compiled_settings_are_independent_copies(self):
        first = load_definitions(DEFINITIONS)
        config = manager_config(first['codex']); config['label'] = 'changed'
        first['codex'].terminal['login']['args'].append('changed')
        second = load_definitions(DEFINITIONS)
        self.assertEqual(second['codex'].terminal['login']['args'], ['login'])
        self.assertNotEqual(manager_config(second['codex'])['label'], 'changed')

    def test_builtins_keep_order_aliases_and_native_login_commands(self):
        registry = load_registry(self.config)
        self.assertEqual(list(registry.settings), ['codex', 'opencode', 'claude', 'copilot', 'antigravity'])
        for alias, provider in (('agy', 'antigravity'), ('claude-code', 'claude'), ('copilot-cli', 'copilot')):
            self.assertEqual(registry.resolve(alias).id, provider)
        self.assertEqual(registry.resolve('opencode').login.args, ('auth', 'login', '--standalone'))
        self.assertEqual(registry.resolve('claude').home.environment, 'CLAUDE_CONFIG_DIR')
        self.assertEqual(registry.resolve('copilot').home.environment, 'COPILOT_HOME')
        self.assertTrue(registry.resolve('antigravity').home.shared)
        self.assertEqual(registry.settings['codex']['run_args'], [])

    def test_budget_selection_is_declared_and_defaults_without_a_reader(self):
        registry = load_registry(self.config)
        for provider in ('codex', 'claude', 'copilot', 'antigravity'):
            policy = registry.resolve(provider)
            self.assertEqual(policy.budget.selection.strategy, 'max-remaining')
            self.assertEqual(policy.capabilities()['budget']['automaticSelection'], 'max-remaining')
        self.local('example', native_manifest('example'))
        self.assertEqual(load_registry(self.config).resolve('example').budget.selection.strategy, 'default')
        self.assertEqual(registry.resolve('opencode').budget.selection.strategy, 'comparable-or-default')

    def test_invalid_budget_ranking_policies_are_rejected_without_echoing_values(self):
        for selection in ({}, {'strategy': 'private-strategy'}, {'strategy': 'max-remaining'},
                {'strategy': 'max-remaining', 'buckets': ['private invalid pattern']},
                {'strategy': 'max-remaining', 'buckets': ['*'], 'model_families': []},
                {'strategy': 'max-remaining', 'buckets': ['*'], 'model_families': {'opus': []}},
                {'strategy': 'max-remaining', 'buckets': ['*'], 'model_families': {'opus': ['private\nvalue']}},
                {'strategy': 'max-remaining', 'buckets': ['*'], 'command': 'private-command'}):
            value = document('codex'); value['terminal']['budget']['selection'] = selection
            with self.subTest(selection=selection), self.assertRaises(ManifestError) as caught:
                parse_definition(value)
            self.assertNotIn('private', str(caught.exception))
        value = document('opencode')
        value['terminal']['budget']['selection'] = {'strategy': 'max-remaining', 'buckets': ['*']}
        with self.assertRaisesRegex(ManifestError, 'enabled reader'): parse_definition(value)

    def test_alias_preferences_replace_arrays_without_mutating_the_next_load(self):
        self.config.write_text('[providers.claude-code]\nlaunch_args=["--verbose"]\n')
        self.assertEqual(load_registry(self.config).resolve('claude').settings['launch_args'], ['--verbose'])
        self.config.write_text('')
        self.assertEqual(load_registry(self.config).resolve('claude').settings['launch_args'], [])

    def test_removed_saved_auth_preferences_are_errors_without_echoing_values(self):
        for field in ('auth_mode', 'auth_adapter', 'auth_files', 'auth_recovery', 'cross_account_resume'):
            self.config.write_text('[providers.claude-code]\n' + field + '="private-invalid-value"\n')
            with self.subTest(field=field), self.assertRaisesRegex(Error, 'Unsupported profile-only preferences') as caught:
                load(self.config)
            self.assertNotIn('private-invalid-value', str(caught.exception))

    def test_custom_provider_preferences_require_a_definition(self):
        self.config.write_text('[providers.example]\nexecutable="example"\n')
        with self.assertRaisesRegex(Error, 'requires a manifest'): load(self.config)

    def test_explicit_missing_configuration_and_malformed_tables_are_errors(self):
        with self.assertRaisesRegex(Error, 'does not exist'): load(self.base / 'missing.toml')
        for text in ('providers = []', '[providers]\nclaude="wrong"'):
            self.config.write_text(text)
            with self.assertRaises(Error): load(self.config)

    def test_definition_precedes_preferences_and_edits_are_not_cached(self):
        source = (DEFINITIONS / 'codex.toml').read_text()
        self.local('codex', source.replace('executable = "codex"', 'executable = "local-codex"'))
        self.assertEqual(load_registry(self.config).resolve('codex').settings['executable'], 'local-codex')
        self.config.write_text('[providers.codex]\nexecutable="preferred-codex"\n')
        self.assertEqual(load_registry(self.config).resolve('codex').settings['executable'], 'preferred-codex')
        self.config.write_text(''); (self.definitions / 'codex.toml').unlink()
        self.assertEqual(load_registry(self.config).resolve('codex').settings['executable'], 'codex')

    def test_local_aliases_are_merged_and_builtin_collisions_are_rejected(self):
        self.local('example', native_manifest('example', ('ex',)))
        self.config.write_text('[providers.ex]\nexecutable="preferred-example"\n')
        self.assertEqual(load_registry(self.config).resolve('example').settings['executable'], 'preferred-example')
        self.local('example', native_manifest('example', ('codex',)))
        with self.assertRaisesRegex(Error, 'duplicate provider name or alias'): load(self.config)

    def test_local_provider_cannot_claim_a_disabled_cross_profile_adapter(self):
        source = (DEFINITIONS / 'copilot.toml').read_text().replace(
            'cross_environment_resume = "copilot-session-link"', 'cross_environment_resume = "codex-linux-history"')
        self.local('copilot', source)
        with self.assertRaises(Error): load(self.config)

    def test_local_definition_inherits_the_bundled_continuation_reason(self):
        source = (DEFINITIONS / 'antigravity.toml').read_text()
        self.local('antigravity', '\n'.join(line for line in source.splitlines() if not line.startswith('cross_environment_resume_reason')))
        self.assertEqual(load_registry(self.config).resolve('antigravity').sessions.cross_environment_resume_reason,
            document('antigravity')['terminal']['sessions']['cross_environment_resume_reason'])
