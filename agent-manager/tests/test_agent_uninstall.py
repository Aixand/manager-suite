"""Standalone provider CLI removal shares the guarded software lifecycle."""

import contextlib
import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_manager.errors import Error
from agent_manager.interfaces.cli.dispatch import execute
from agent_manager.interfaces.cli.parser import Parser
from agent_manager.providers.installation import uninstall


class UninstallTests(unittest.TestCase):
    def setUp(self):
        self.manager = SimpleNamespace(provider="codex")
        self.plan = dict(
            path="/private/fixture/bin/codex",
            version="0.166.0",
            method="installer",
            automatic=True,
            reason=None,
            manualCommands=[],
            targets=["/private/fixture/bin/codex"],
            commandTargets=[],
            revision="sha256:fixture",
        )
        self.output = io.StringIO()

    def args(self, *values):
        return uninstall.parser(Parser).parse_args(list(values))

    def invoke(self, *values, answer="", interactive=True):
        self.output = io.StringIO()
        self.output.isatty = lambda: interactive
        with (
            patch.object(uninstall, "Manager", return_value=self.manager),
            patch.object(uninstall.software, "plan", return_value=self.plan),
            patch.object(uninstall.sys.stdin, "isatty", return_value=interactive),
            contextlib.redirect_stdout(self.output),
        ):
            return uninstall.command(
                self.args(*values), input_fn=lambda _: answer
            )

    def test_dry_run_describes_executable_and_preserved_native_data(self):
        with patch.object(uninstall.software, "perform") as perform:
            self.assertEqual(self.invoke("codex", "--dry-run"), 0)
        perform.assert_not_called()
        self.assertIn("/private/fixture/bin/codex", self.output.getvalue())
        self.assertIn("remove: /private/fixture/bin/codex", self.output.getvalue())
        self.assertIn("preserve: profiles, authentication", self.output.getvalue())

    def test_exact_confirmation_uninstalls_only_through_software_guard(self):
        result = dict(uninstalled=True, disabled=True, accountsPreserved=True)
        with patch.object(uninstall.software, "perform", return_value=result) as perform:
            self.assertEqual(self.invoke("codex", answer="UNINSTALL CODEX CLI"), 0)
        perform.assert_called_once_with(
            self.manager,
            "uninstall",
            revision="sha256:fixture",
            confirmed=True,
        )
        self.assertIn("Preserved profiles", self.output.getvalue())

    def test_declined_or_noninteractive_uninstall_never_mutates(self):
        with patch.object(uninstall.software, "perform") as perform:
            self.assertEqual(self.invoke("codex", answer="no"), 0)
            with self.assertRaisesRegex(Error, "interactive terminal or --yes"):
                self.invoke("codex", interactive=False)
        perform.assert_not_called()

    def test_unverified_or_external_installation_requires_manual_removal(self):
        self.plan.update(
            automatic=False,
            reason="This CLI is managed externally.",
            manualCommands=["manual-remove"],
        )
        with patch.object(uninstall.software, "perform") as perform:
            with self.assertRaisesRegex(Error, "managed externally"):
                self.invoke("codex", "--yes")
        perform.assert_not_called()


class UninstallRoutingTests(unittest.TestCase):
    def test_root_uninstall_routes_before_provider_manager(self):
        with (
            patch(
                "agent_manager.providers.installation.uninstall.command",
                return_value=0,
            ) as command,
            patch("agent_manager.interfaces.cli.dispatch.Manager") as manager,
        ):
            self.assertEqual(execute(["uninstall", "codex", "--yes"]), 0)
        manager.assert_not_called()
        self.assertEqual(command.call_args.args[0].target, "codex")


if __name__ == "__main__":
    unittest.main()
