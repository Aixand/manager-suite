"""CLI/SDK/MCP configuration consistency and provider readiness diagnostics."""
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import unittest
import uuid
from unittest.mock import patch

from agent_manager import Client, Error, Manager
from agent_manager.interfaces.cli.dispatch import execute
from agent_manager.interfaces.management_mcp import LocalBackend
from tests.support import FakeRuntime
from tests.test_budget_interfaces import BudgetFixture


class ConfigurationServicesTests(BudgetFixture, unittest.TestCase):
    def test_argument_revision_rejects_stale_writes_and_preserves_other_scope(self):
        first = self.manager.launch_arguments()
        changed = self.manager.set_launch_arguments(['--global'], expected_revision=first['revision'])
        self.assertNotEqual(first['revision'], changed['revision'])
        with self.assertRaisesRegex(Error, 'arguments changed'):
            self.manager.set_launch_arguments(['--stale'], expected_revision=first['revision'])
        scoped = self.manager.launch_arguments('work')
        self.manager.set_launch_arguments(['--scoped'], 'work', expected_revision=scoped['revision'])
        self.assertEqual(self.manager.saved_launch_args('work'), ['--global', '--scoped'])
        with self.assertRaises(Error): self.manager.set_launch_arguments('--not-an-array')

    def test_long_lived_manager_explicitly_refreshes_external_changes(self):
        other = Manager('codex', self.config, self.root)
        other.set_launch_arguments(['--external'])
        self.assertEqual(self.manager.config['launch_args'], [])
        result = self.manager.configuration('work', effective=True)
        self.assertEqual(result['arguments']['effective'], ['--external'])
        self.assertEqual(self.manager.config['launch_args'], ['--external'])
        self.assertEqual(result['environment_id'], self.manager.resolve_profile('work').environment_id)
        self.assertNotIn('resources', result)

    def test_refresh_does_not_change_in_flight_launch_configuration(self):
        self.manager.set_launch_arguments(['--original'])
        other = Manager('codex', self.config, self.root)
        def invoke(home, arguments, **options):
            other.set_launch_arguments(['--future'])
            self.manager.refresh_configuration()
            self.assertEqual(self.manager.config['launch_args'], ['--original'])
            self.assertIn('--original', arguments)
            self.assertNotIn('--future', arguments)
            return 0
        with patch.object(self.manager, '_invoke_native', side_effect=invoke):
            self.assertEqual(self.manager._run_native('work', []), 0)
        self.assertEqual(self.manager.config['launch_args'], ['--future'])

    def test_instruction_editor_retains_draft_after_concurrent_change(self):
        import os
        import sys
        from agent_manager.interfaces.cli.instructions_config import edit
        from types import SimpleNamespace
        original = self.manager.set_native_instructions('Original.')
        def editor(argv):
            Path(argv[-1]).write_text('My unsaved edit.')
            self.manager.set_native_instructions('Concurrent change.', expected_revision=original['revision'])
            return SimpleNamespace(returncode=0)
        with patch.dict(os.environ, VISUAL=sys.executable), patch('agent_manager.interfaces.cli.instructions_config.subprocess.run', side_effect=editor):
            with self.assertRaisesRegex(Error, 'Instruction file changed'): edit(self.manager)
        self.assertEqual(self.manager.native_instructions()['content'], 'Concurrent change.')
        drafts = list((self.manager.root / 'instruction-drafts').glob('*.md'))
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].read_text(), 'My unsaved edit.')

    def test_native_instruction_revision_is_checked_before_file_write(self):
        first = self.manager.native_instructions()
        self.assertFalse(first['exists'])
        second = self.manager.set_native_instructions('Shared rule.', expected_revision=first['revision'])
        with self.assertRaisesRegex(Error, 'Instruction file changed'):
            self.manager.set_native_instructions('Stale rule.', expected_revision=first['revision'])
        self.assertEqual(self.manager.native_instructions()['content'], second['content'])
        self.manager.alias_profile('alias', 'work')
        local = self.manager.native_instructions('alias')
        self.manager.set_native_instructions('Local rule.', 'alias', expected_revision=local['revision'])
        self.assertEqual(self.manager.native_instructions('work')['content'], 'Local rule.')

    def test_argument_writer_preserves_a_symlinked_configuration_file(self):
        import os
        if os.name == 'nt': self.skipTest('Windows symlink privileges vary')
        target = self.base / 'actual settings.toml'
        self.config.rename(target)
        self.config.symlink_to(target)
        self.manager.set_launch_arguments(['--new'])
        self.assertTrue(self.config.is_symlink())
        fresh = Manager('codex', self.config, self.root)
        self.assertEqual(fresh.config['launch_args'], ['--new'])

    def test_effective_cli_and_headless_results_agree(self):
        self.manager.set_launch_arguments(['--model', 'example'], 'work')
        output = io.StringIO()
        with redirect_stdout(output):
            execute(['--config', str(self.config), '--home', str(self.root), 'codex',
                'config', 'show', '--profile', 'work', '--effective', '--json'])
        result = json.loads(output.getvalue())
        self.assertEqual(result, self.manager.configuration('work', effective=True))
        self.assertEqual(result['native_model'], 'example')
        self.assertEqual(result['arguments']['profile']['args'], ['--model', 'example'])

    def test_doctor_default_does_not_refresh_native_login_or_allowance(self):
        original = self.manager.state_path.read_bytes()
        with patch.object(self.manager, 'login_status', side_effect=AssertionError('unexpected login probe')), \
             patch('agent_manager.budgets.adapters.read', side_effect=AssertionError('unexpected budget probe')):
            result = self.manager.readiness('work')
        self.assertEqual(result['login']['state'], 'unknown')
        self.assertTrue(result['ready'])
        self.assertEqual(self.manager.state_path.read_bytes(), original)
        self.assertIn('budget', result['features'])

    def test_doctor_explains_qualification_and_signed_out_separately(self):
        from agent_manager.providers.installation.engine import Installation
        with patch('agent_manager.providers.installation.engine.inspect',
                return_value=Installation(Path('/fixture/claude'), 'installer', '2.1.316', None)), \
             patch.object(self.managers['claude'], 'login_status', return_value=dict(state='signed-out', verification='native-status')):
            result = self.managers['claude'].readiness('work', refresh=True)
        codes = {row['code'] for row in result['issues']}
        self.assertIn('unsupported-budget', codes)
        self.assertIn('qualification-pending-budget', codes)
        self.assertIn('authentication', codes)
        self.assertFalse(result['ready'])

    def test_custom_manifest_can_declare_instruction_paths(self):
        from tests.support import native_manifest
        config = self.base / 'custom.toml'; config.write_text('')
        definitions = self.base / 'providers'; definitions.mkdir()
        (definitions / 'example.toml').write_text(native_manifest('example') +
            '\n[terminal.instructions]\nstrategy="home"\nfilename="PERSONAL.md"\n')
        manager = Manager('example', config, self.root)
        manager.new_profile('main')
        self.assertEqual(manager.native_instructions('main')['path'], str(manager.home('main') / 'PERSONAL.md'))
        self.assertTrue(manager.provider_spec.capabilities()['nativeInstructions']['supported'])

    def test_manifest_rejects_unsafe_or_ambiguous_instruction_paths(self):
        from copy import deepcopy
        import tomllib
        from agent_manager.providers.schema import parse_definition, ManifestError
        original = tomllib.loads(Path('agent_manager/providers/definitions/codex.toml').read_text())
        policies = [dict(strategy='home', filename='../AGENTS.md'),
            dict(strategy='home', filename='AGENTS.md', overrides=['AGENTS.md']),
            dict(strategy='global', filename='AGENTS.md', paths=['~/../secret.md']),
            dict(strategy='global', filename='AGENTS.md', paths=[])]
        for policy in policies:
            document = deepcopy(original); document['terminal']['instructions'] = policy
            with self.subTest(policy=policy), self.assertRaises(ManifestError): parse_definition(document)


class AsyncConfigurationServicesTests(BudgetFixture, unittest.IsolatedAsyncioTestCase):
    async def test_sdk_and_mcp_use_the_same_revisions_and_idempotent_writes(self):
        client = Client('codex', config=self.config, root=self.root, runtime=FakeRuntime())
        backend = LocalBackend(config=self.config, home=self.root)
        original = await client.launch_arguments('work')
        args = dict(provider='codex', profile='work', args=['--scoped'], expectedRevision=original['revision'])
        request = str(uuid.uuid4())
        first = await backend.execute('set_launch_arguments', args, request)
        self.assertEqual(first, await backend.execute('set_launch_arguments', args, request))
        current = await client.launch_arguments('work')
        self.assertEqual(current, first['arguments'])
        config = await backend.execute('get_configuration', dict(provider='codex', profile='work', effective=True), str(uuid.uuid4()))
        self.assertEqual(config['configuration'], await client.configuration('work', effective=True))
        instructions = await client.native_instructions('work')
        result = await backend.execute('set_native_instructions', dict(provider='codex', profile='work',
            text='Headless rule.', expectedRevision=instructions['revision']), str(uuid.uuid4()))
        self.assertEqual(result['instructions'], await client.native_instructions('work'))
        readonly = await backend.execute('get_native_instructions', dict(provider='codex', profile='work'), 'read')
        self.assertEqual(readonly, result)
