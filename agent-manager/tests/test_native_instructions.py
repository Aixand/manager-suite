"""Native instruction files, editor selection and additive shared guidance."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_manager import Error
from agent_manager.interfaces.cli.dispatch import execute
from agent_manager.providers import native_instructions as instructions
from agent_manager.interfaces.cli import instructions_config as editor_cli
from agent_manager.runtime.execution import launch_home
from tests.test_budget_interfaces import BudgetFixture


class NativeInstructionsTests(BudgetFixture, unittest.TestCase):
    def put(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode())

    def invoke(self, provider, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            execute(['--config', str(self.config), '--home', str(self.root), provider, 'config', 'instructions', *args])
        return output.getvalue()

    def test_path_and_show_are_read_only_and_missing_files_are_actionable(self):
        path = instructions.native_path(self.manager, 'work')
        self.assertEqual(self.invoke('codex', 'path').strip(), str(path))
        result = json.loads(self.invoke('codex', 'show', '--json'))
        self.assertFalse(result['exists']); self.assertEqual(result['content'], '')
        self.assertIn('config instructions edit', self.invoke('codex', 'show'))
        self.assertFalse(path.exists())

    def test_native_paths_follow_each_provider_environment(self):
        expected = {
            'codex': self.manager.home('work') / 'AGENTS.md',
            'claude': self.managers['claude'].home('work') / 'CLAUDE.md',
            'copilot': self.managers['copilot'].home('work') / 'copilot-instructions.md',
            'opencode': self.managers['opencode'].home('work') / 'config/opencode/AGENTS.md',
            'antigravity': Path.home() / '.gemini/GEMINI.md',
        }
        for provider, path in expected.items():
            with self.subTest(provider=provider):
                self.assertEqual(instructions.native_path(self.managers[provider], 'work'), path)
                self.assertEqual(json.loads(self.invoke(provider, 'path', '--profile', 'work', '--json'))['path'], str(path))


    def test_codex_override_precedence_and_empty_override_fallback(self):
        home = self.manager.home('work')
        base, override = home / 'AGENTS.md', home / 'AGENTS.override.md'
        self.put(base, 'Base guidance.')
        self.put(override, 'Override guidance.')
        self.assertEqual(instructions.native_path(self.manager, 'work'), override)
        self.assertEqual(instructions.read(base), 'Base guidance.')
        self.assertEqual(instructions.read(override), 'Override guidance.')
        self.put(override, '')
        self.assertEqual(instructions.native_path(self.manager, 'work'), base)

    def test_visual_takes_precedence_and_command_arguments_are_literal(self):
        with patch.dict(os.environ, VISUAL=f'{sys.executable} -i', EDITOR='does-not-exist'):
            self.assertEqual(editor_cli.editor_command(), [sys.executable, '-i'])
        with patch.dict(os.environ, VISUAL='', EDITOR=f'"{sys.executable}" -i'):
            self.assertEqual(editor_cli.editor_command(), [sys.executable, '-i'])

    def test_editor_create_existing_edit_failure_and_cancellation(self):
        path = instructions.native_path(self.manager, 'work')
        def editor(argv):
            self.assertEqual(argv[:-1], [sys.executable])
            self.assertTrue(Path(argv[-1]).name.endswith('-AGENTS.md'))
            draft = Path(argv[-1])
            self.put(draft, instructions.read(draft) + 'New rule.\n')
            return SimpleNamespace(returncode=0)
        with patch.dict(os.environ, VISUAL=sys.executable), patch.object(editor_cli.subprocess, 'run', side_effect=editor):
            editor_cli.edit(self.manager); editor_cli.edit(self.manager)
        self.assertEqual(instructions.read(path), 'New rule.\nNew rule.\n')
        with patch.dict(os.environ, VISUAL=sys.executable), patch.object(editor_cli.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(Error, 'Editor exited'): editor_cli.edit(self.manager)
        self.assertEqual(instructions.read(path), 'New rule.\nNew rule.\n')
        other = self.managers['copilot']
        with patch.dict(os.environ, VISUAL='', EDITOR=''), patch.object(editor_cli.shutil, 'which', return_value='/usr/bin/vi'):
            self.assertIsNone(editor_cli.edit(other, picker=lambda *a: None))
        self.assertFalse(instructions.native_path(other, 'work').exists())

    def test_profile_edit_uses_override_and_prints_its_precedence(self):
        override = self.manager.home('work') / 'AGENTS.override.md'
        self.put(override, 'Existing override.')
        output = io.StringIO()
        with patch.dict(os.environ, VISUAL=sys.executable), redirect_stderr(output), \
             patch.object(editor_cli.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as editor:
            editor_cli.edit(self.manager, 'work')
        self.assertTrue(Path(editor.call_args.args[0][-1]).name.endswith('-AGENTS.override.md'))
        self.assertIn('takes precedence', output.getvalue())
        self.assertEqual(override.read_text(), 'Existing override.')

    def test_edit_succeeds_while_environment_is_in_use(self):
        from agent_manager.storage.locks import locks
        target = self.manager.resolve_profile('work')
        path = instructions.native_path(self.manager, target)
        self.put(path, 'Original guidance.')

        def editor(argv):
            self.put(Path(argv[-1]), 'Edited guidance.')
            return SimpleNamespace(returncode=0)

        with locks(self.manager.lock_root, 'environment-' + target.environment_id), \
             patch.dict(os.environ, VISUAL=sys.executable), \
             patch.object(editor_cli.subprocess, 'run', side_effect=editor):
            editor_cli.edit(self.manager, 'work')
        self.assertEqual(path.read_text(), 'Edited guidance.')
        self.assertEqual(list((self.manager.root / 'instruction-drafts').iterdir()), [])

    def test_concurrent_change_retains_draft_and_preserves_newer_instructions(self):
        path = instructions.native_path(self.manager, 'work')
        self.put(path, 'Original guidance.')

        def editor(argv):
            self.put(Path(argv[-1]), 'Draft guidance.')
            instructions.write(self.manager, 'Concurrent guidance.', 'work')
            return SimpleNamespace(returncode=0)

        with patch.dict(os.environ, VISUAL=sys.executable), \
             patch.object(editor_cli.subprocess, 'run', side_effect=editor):
            with self.assertRaisesRegex(Error, 'Instruction file changed'):
                editor_cli.edit(self.manager, 'work')
        self.assertEqual(path.read_text(), 'Concurrent guidance.')
        drafts = list((self.manager.root / 'instruction-drafts').iterdir())
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].read_text(), 'Draft guidance.')

    def test_symlink_instruction_file_is_preserved(self):
        if os.name == 'nt': self.skipTest('Windows symlink privileges vary')
        path = instructions.native_path(self.manager, 'work')
        target = self.base / 'linked rules.md'; self.put(target, 'Original linked rules.')
        path.symlink_to(target)
        instructions.write(self.manager, 'Edited linked rules.', 'work')
        self.assertTrue(path.is_symlink())
        self.assertEqual('Edited linked rules.', target.read_text())

    def test_cli_rejects_forwarded_arguments(self):
        with self.assertRaisesRegex(Error, 'does not accept forwarded'):
            self.invoke('codex', 'edit', '--', '--yolo')

    def test_cli_edit_runs_an_editor_with_a_literal_path_and_emits_json(self):
        script = self.base / 'my editor.py'
        script.write_text('import sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text("Editor saved guidance.\\n", encoding="utf-8")\n')
        with patch.dict(os.environ, VISUAL='', EDITOR=f'"{sys.executable}" "{script}"'):
            row = json.loads(self.invoke('codex', 'edit', '--json'))
        self.assertEqual(Path(row['path']).read_text(), 'Editor saved guidance.\n')
        self.assertEqual(self.invoke('codex', 'show'), 'Editor saved guidance.\n')



if __name__ == '__main__': unittest.main()
