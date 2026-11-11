"""Destructive reset planning is explicit, scoped and fail-closed."""

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_manager.errors import Error
from agent_manager.interfaces.cli import reset as reset_cli
from agent_manager.interfaces.cli.dispatch import execute
from agent_manager.interfaces.cli.parser import Parser
from agent_manager.manager import Manager
from agent_manager.storage import reset


class ResetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.config = self.base / "config/providers.toml"
        self.config.parent.mkdir()
        self.root = self.base / "state"
        self.codex_default = self.base / "default-codex"
        self.codex_external = self.base / "external-codex"
        self.claude_default = self.base / "default-claude"
        self.claude_external = self.base / "external-claude"
        for path in (
            self.codex_default,
            self.codex_external,
            self.claude_default,
            self.claude_external,
        ):
            path.mkdir()
            (path / "marker").write_text(path.name)
        self.config.write_text(
            "[providers.codex]\nnative_home = "
            + json.dumps(str(self.codex_default))
            + "\n"
            "[providers.claude]\nnative_home = "
            + json.dumps(str(self.claude_default))
            + "\n"
        )
        self.codex = Manager("codex", self.config, self.root)
        self.codex.connect_profile("default", self.codex_default)
        self.codex.connect_profile("other", self.codex_external)
        self.codex.new_profile("managed")
        self.claude = Manager("claude", self.config, self.root)
        self.claude.connect_profile("default", self.claude_default)
        self.claude.connect_profile("other", self.claude_external)
        cli_inventory = patch.object(reset_cli, "_cli_plans", return_value=[])
        cli_inventory.start()
        self.addCleanup(cli_inventory.stop)

    def args(self, *values):
        return reset_cli.parser(Parser).parse_args(list(values))

    def invoke(
        self,
        *values,
        answers=(),
        cli_plans=(),
        config_answer="",
        scope_selection=1,
    ):
        prompts = iter(answers)

        def respond(prompt):
            if prompt.startswith("Restore "):
                return config_answer
            return next(prompts)

        output = io.StringIO()
        output.isatty = lambda: True
        with (
            patch.object(reset_cli.sys.stdin, "isatty", return_value=True),
            patch.object(reset_cli, "_cli_plans", return_value=list(cli_plans)),
            contextlib.redirect_stdout(output),
        ):
            code = reset_cli.command(
                self.args(*values),
                config=self.config,
                home=self.root,
                input_fn=respond,
                picker=lambda *args, **kwargs: scope_selection,
            )
        return code, output.getvalue()

    def cli_candidate(self, manager=None, *, automatic=True):
        manager = manager or self.codex
        return manager, dict(
            path=str(self.base / ".local/bin" / manager.provider),
            version="1.2.3",
            method="installer",
            automatic=automatic,
            reason=None if automatic else "Manual removal required.",
            manualCommands=["manual-remove"] if not automatic else [],
            targets=[str(self.base / ".local/bin" / manager.provider)],
            commandTargets=[],
            revision="sha256:fixture",
        )

    def test_dry_run_classifies_default_and_non_default_homes_without_changes(self):
        code, output = self.invoke("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn(str(self.codex_default), output)
        self.assertIn(str(self.codex_external), output)
        self.assertTrue(self.codex.state_path.exists())
        self.assertTrue(self.config.exists())

    def test_first_confirmation_cancels_without_side_effects(self):
        code, output = self.invoke(answers=["no"])
        self.assertEqual(code, 0)
        self.assertIn("cancelled", output)
        self.assertTrue(self.codex_external.exists())
        self.assertTrue(self.codex.state_path.exists())

    def test_interactive_reset_preserves_defaults_without_second_phrase(self):
        code, output = self.invoke(answers=["RESET", ""])
        self.assertEqual(code, 0)
        self.assertIn("completed", output)
        self.assertTrue(self.codex_default.exists())
        self.assertTrue(self.claude_default.exists())
        self.assertFalse(self.codex_external.exists())
        self.assertFalse(self.claude_external.exists())
        self.assertFalse(self.root.exists())
        self.assertFalse(self.config.exists())

    def test_interactive_second_confirmation_deletes_default_homes(self):
        self.invoke(answers=["RESET", "DELETE DEFAULT HOMES"])
        for path in (
            self.codex_default,
            self.codex_external,
            self.claude_default,
            self.claude_external,
        ):
            self.assertFalse(path.exists())

    def test_interactive_reset_separately_confirms_provider_cli_uninstall(self):
        candidate = self.cli_candidate()
        with (
            patch(
                "agent_manager.providers.installation.software.validate",
                return_value=candidate[1],
            ) as validate,
            patch(
                "agent_manager.providers.installation.software.perform",
                return_value=dict(
                    uninstalled=True, disabled=True, accountsPreserved=True
                ),
            ) as perform,
        ):
            code, output = self.invoke(
                "codex",
                answers=["RESET", "", "UNINSTALL PROVIDER CLIS"],
                cli_plans=[candidate],
            )
        self.assertEqual(code, 0)
        validate.assert_called_once_with(
            self.codex, "uninstall", "sha256:fixture", True
        )
        perform.assert_called_once_with(
            self.codex,
            "uninstall",
            revision="sha256:fixture",
            confirmed=True,
        )
        self.assertIn("Uninstalled provider CLIs", output)

    def test_reset_preserves_provider_cli_when_uninstall_confirmation_is_declined(self):
        candidate = self.cli_candidate()
        with patch(
            "agent_manager.providers.installation.software.perform"
        ) as perform:
            self.invoke(
                "codex", answers=["RESET", "", ""], cli_plans=[candidate]
            )
        perform.assert_not_called()

    def test_noninteractive_cli_uninstall_requires_explicit_flag(self):
        candidate = self.cli_candidate()
        with (
            patch(
                "agent_manager.providers.installation.software.validate",
                return_value=candidate[1],
            ),
            patch(
                "agent_manager.providers.installation.software.perform",
                return_value=dict(
                    uninstalled=True, disabled=True, accountsPreserved=True
                ),
            ) as perform,
        ):
            self.invoke(
                "codex", "--yes", "--uninstall-clis", cli_plans=[candidate]
            )
        perform.assert_called_once()

    def test_noninteractive_requires_yes_and_separate_default_flag(self):
        with (
            patch.object(reset_cli.sys.stdin, "isatty", return_value=False),
            patch.object(reset_cli.sys.stdout, "isatty", return_value=False),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(Error, "--all-providers or --factory"),
        ):
            reset_cli.command(self.args(), config=self.config, home=self.root)
        with (
            patch.object(reset_cli.sys.stdin, "isatty", return_value=False),
            patch.object(reset_cli.sys.stdout, "isatty", return_value=False),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(
                reset_cli.command(
                    self.args("--factory", "--yes"),
                    config=self.config,
                    home=self.root,
                ),
                0,
            )
        self.assertTrue(self.codex_default.exists())
        self.assertTrue(self.claude_default.exists())

    def test_noninteractive_default_flag_deletes_default_homes(self):
        with contextlib.redirect_stdout(io.StringIO()):
            reset_cli.command(
                self.args("--factory", "--yes", "--include-default-homes"),
                config=self.config,
                home=self.root,
            )
        self.assertFalse(self.codex_default.exists())
        self.assertFalse(self.claude_default.exists())

    def test_provider_reset_keeps_other_provider_and_shared_configuration(self):
        codex_homes = self.codex.managed_homes
        claude_homes = self.claude.managed_homes
        claude_homes.mkdir(parents=True)
        (claude_homes / "preserved").write_text("other provider")
        code, output = self.invoke("codex", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("for codex", output)
        self.assertFalse(self.codex_external.exists())
        self.assertTrue(self.codex_default.exists())
        self.assertFalse(self.codex.state_path.exists())
        self.assertTrue(self.claude_external.exists())
        self.assertTrue(self.claude.state_path.exists())
        self.assertFalse(codex_homes.exists())
        self.assertEqual((claude_homes / "preserved").read_text(), "other provider")
        self.assertTrue(self.config.exists())
        self.assertIn("providers.codex", self.config.read_text())

    def test_all_provider_reset_preserves_manager_configuration_and_services(self):
        provider_definition = self.config.parent / "providers/custom.keep"
        service_definition = self.config.parent / "services/custom.keep"
        provider_definition.parent.mkdir()
        service_definition.parent.mkdir()
        provider_definition.write_text("provider definition")
        service_definition.write_text("service definition")
        self.invoke("--all-providers", "--yes")
        self.assertTrue(self.config.exists())
        self.assertEqual(provider_definition.read_text(), "provider definition")
        self.assertEqual(service_definition.read_text(), "service definition")
        self.assertFalse(self.codex.state_path.exists())
        self.assertFalse(self.claude.state_path.exists())
        self.assertTrue(self.codex_default.exists())
        self.assertTrue(self.claude_default.exists())

    def test_provider_configuration_can_be_restored_without_touching_other_provider(self):
        import tomllib
        from agent_manager.providers.loader import DEFINITIONS

        definitions = self.config.parent / "providers"
        definitions.mkdir()
        override = definitions / "codex.toml"
        override.write_text((DEFINITIONS / "codex.toml").read_text())
        code, output = self.invoke(
            "codex",
            answers=["RESET", ""],
            config_answer="RESET CODEX CONFIG",
        )
        self.assertEqual(code, 0)
        document = tomllib.loads(self.config.read_text())
        self.assertNotIn("codex", document["providers"])
        self.assertIn("claude", document["providers"])
        self.assertFalse(override.exists())
        self.assertIn("restore preference table codex", output)

    def test_provider_config_dry_run_and_noninteractive_flag_are_explicit(self):
        import tomllib

        code, output = self.invoke("codex", "--dry-run", "--reset-config")
        self.assertEqual(code, 0)
        self.assertIn("restore preference table codex", output)
        self.assertIn("codex", tomllib.loads(self.config.read_text())["providers"])
        self.invoke("codex", "--yes", "--reset-config")
        document = tomllib.loads(self.config.read_text())
        self.assertNotIn("codex", document["providers"])
        self.assertIn("claude", document["providers"])

    def test_provider_config_rewrite_preserves_unrelated_toml_values(self):
        import tomllib

        self.config.write_text(
            '# unrelated heading comment\n'
            'title = "keep"\n'
            '[providers.codex]\n'
            f'native_home = {json.dumps(str(self.codex_default))}\n'
            'labels = { team = "review", count = 2 }\n'
            '[providers.claude]\n'
            '# preserve the Claude explanation\n'
            f'native_home = {json.dumps(str(self.claude_default))}\n'
            'run_args = ["--safe", "two words"]\n'
        )
        self.invoke("codex", "--yes", "--reset-config")
        document = tomllib.loads(self.config.read_text())
        self.assertEqual(document["title"], "keep")
        self.assertNotIn("codex", document["providers"])
        self.assertEqual(
            document["providers"]["claude"]["run_args"],
            ["--safe", "two words"],
        )
        rewritten = self.config.read_text()
        self.assertIn("# unrelated heading comment", rewritten)
        self.assertIn("# preserve the Claude explanation", rewritten)

    def test_failed_provider_config_rewrite_restores_manifest_and_configuration(self):
        from agent_manager.providers.loader import DEFINITIONS

        definitions = self.config.parent / "providers"
        definitions.mkdir()
        override = definitions / "codex.toml"
        override.write_text((DEFINITIONS / "codex.toml").read_text())
        original_config = self.config.read_bytes()
        original_manifest = override.read_bytes()
        plan = reset.create_plan(
            provider="codex",
            config=self.config,
            root=self.root,
            reset_provider_config=True,
        )
        with (
            patch.object(reset, "atomic", side_effect=OSError("write failure")),
            self.assertRaises(OSError),
        ):
            reset.perform(plan)
        self.assertEqual(self.config.read_bytes(), original_config)
        self.assertEqual(override.read_bytes(), original_manifest)
        self.assertTrue(self.codex.state_path.exists())

    def test_interactive_scope_can_cancel_before_planning(self):
        code, output = self.invoke(scope_selection=None)
        self.assertEqual(code, 0)
        self.assertIn("cancelled", output)
        self.assertTrue(self.codex.state_path.exists())

    def test_provider_alias_and_default_confirmation_apply_only_to_selection(self):
        self.invoke("claude-code", "--yes", "--include-default-homes")
        self.assertFalse(self.claude_default.exists())
        self.assertFalse(self.claude_external.exists())
        self.assertTrue(self.codex_default.exists())
        self.assertTrue(self.codex_external.exists())
        self.assertTrue(self.codex.state_path.exists())

    def test_plan_change_after_confirmation_fails_without_deleting(self):
        answers = iter(("RESET", ""))

        def respond(_):
            value = next(answers)
            if value == "":
                self.config.write_text(self.config.read_text() + "\n")
            return value

        output = io.StringIO()
        output.isatty = lambda: True
        with (
            patch.object(reset_cli.sys.stdin, "isatty", return_value=True),
            contextlib.redirect_stdout(output),
            self.assertRaisesRegex(Error, "plan changed"),
        ):
            reset_cli.command(
                self.args("--factory"),
                config=self.config,
                home=self.root,
                input_fn=respond,
            )
        self.assertTrue(self.codex_external.exists())
        self.assertTrue(self.codex.state_path.exists())

    def test_active_managed_run_blocks_planning(self):
        state = self.codex.state()
        target = self.codex.resolve_profile("other")
        state["runs"]["active"] = dict(
            pid=os.getpid(), environment_id=target.environment_id
        )
        self.codex.save(state)
        with self.assertRaisesRegex(Error, "Stop active codex run"):
            reset.create_plan(provider="codex", config=self.config, root=self.root)

    def test_user_home_is_never_an_accepted_registered_target(self):
        manager = Manager("opencode", self.config, self.root)
        manager.connect_profile("unsafe", Path.home())
        with self.assertRaisesRegex(Error, "user home directory"):
            reset.create_plan(provider="opencode", config=self.config, root=self.root)

    def test_registered_home_replaced_by_symlink_is_refused(self):
        actual = self.codex_external.with_name("external-codex-actual")
        self.codex_external.rename(actual)
        self.codex_external.symlink_to(actual, target_is_directory=True)
        with self.assertRaisesRegex(Error, "symbolic link"):
            reset.create_plan(provider="codex", config=self.config, root=self.root)

    def test_staging_failure_rolls_back_every_renamed_target(self):
        plan = reset.create_plan(provider="codex", config=self.config, root=self.root)
        original = Path.rename
        staged = 0

        def rename(path, target):
            nonlocal staged
            if ".agent-manager-reset-" in Path(target).name:
                staged += 1
                if staged == 2:
                    raise OSError("fixture staging failure")
            return original(path, target)

        with (
            patch.object(Path, "rename", autospec=True, side_effect=rename),
            self.assertRaises(OSError),
        ):
            reset.perform(plan)
        self.assertTrue(self.codex_external.exists())
        self.assertTrue(self.codex.state_path.exists())


class ResetRoutingTests(unittest.TestCase):
    def test_root_reset_routes_before_provider_manager(self):
        for argv, provider in (
            (["reset"], None),
            (["reset", "codex", "--yes"], "codex"),
        ):
            with (
                self.subTest(argv=argv),
                patch(
                    "agent_manager.interfaces.cli.reset.command", return_value=0
                ) as command,
                patch("agent_manager.interfaces.cli.dispatch.Manager") as manager,
            ):
                self.assertEqual(execute(argv), 0)
                manager.assert_not_called()
                self.assertEqual(command.call_args.args[0].provider, provider)

    def test_help_and_unknown_provider_are_clear(self):
        with (
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as error,
        ):
            execute(["reset", "--help"])
        self.assertEqual(error.exception.code, 0)
        with (
            tempfile.TemporaryDirectory() as folder,
            self.assertRaisesRegex(Error, "Unknown provider"),
        ):
            config = Path(folder) / "providers.toml"
            config.write_text("")
            reset.create_plan(
                provider="missing", config=config, root=Path(folder) / "state"
            )

    def test_factory_dry_run_works_when_selected_configuration_is_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            args = reset_cli.parser(Parser).parse_args(["--factory", "--dry-run"])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(
                    reset_cli.command(
                        args,
                        config=base / "missing.toml",
                        home=base / "state",
                    ),
                    0,
                )
            self.assertIn("factory reset", output.getvalue())


if __name__ == "__main__":
    unittest.main()
