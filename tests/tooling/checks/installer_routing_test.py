"""Package installation always selects the current product installer."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "manager_dev", ROOT / "scripts/manager_dev.py"
)
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


class InstallerRoutingTests(unittest.TestCase):
    def command(self, *arguments):
        with patch.object(
            sys, "argv", [str(ROOT / "scripts/manager_dev.py"), *arguments]
        ):
            with patch.object(HELPER.subprocess, "call", return_value=0) as call:
                self.assertEqual(HELPER.main(), 0)
        command = call.call_args.args[0]
        self.assertEqual(command[2], str(ROOT / "scripts/dev/cli.py"))
        return command

    def test_agent_lifecycle_without_options_still_selects_product(self):
        for operation in ("install", "reinstall", "uninstall"):
            with self.subTest(operation=operation):
                command = self.command("--child", "agent-manager", operation)
                self.assertEqual(
                    command[-2:], ["--product", "manager-suite/agent-manager"]
                )

    def test_explicit_selection_is_preserved(self):
        for flag, value in (
            ("--product", "manager-suite/skill-manager"),
            ("--component", "filesystem"),
        ):
            with self.subTest(flag=flag):
                command = self.command(
                    "--child", "agent-manager", "package", flag, value
                )
                self.assertEqual(command[-2:], [flag, value])
                self.assertNotIn("manager-suite/agent-manager", command)

    def test_suite_does_not_select_a_child(self):
        command = self.command("package", "--output", "release")
        self.assertNotIn("--product", command)

    def test_unknown_installer_option_is_rejected(self):
        result = subprocess.run(
            [sys.executable, "-B", ROOT / "scripts/manager_dev.py", "--unknown-installer-option", "help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments: --unknown-installer-option", result.stderr)

    def fixture_workspace(self, base):
        workspace = base / "workspace"
        workspace.mkdir()
        (workspace / "installation.toml").write_text("""schema = 2
profile = "workspace"
[workspace]
name = "manager-suite"
version = "1.0.0"
default-product = "manager-suite/agent-manager"
targets = ["linux-x86_64-gnu", "linux-arm64-gnu", "macos-x86_64", "macos-arm64", "windows-x86_64-msvc"]
[components.agent]
adapter = "python"
python = "3.11"
[products."manager-suite/agent-manager"]
adapter = "native"
components = ["agent"]
verification = [["{command:agent}", "--help"]]
[products."manager-suite/agent-manager".commands.agent]
program = "bin/agent"
""")
        return workspace

    def test_doctor_returns_failure_when_uv_is_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.fixture_workspace(Path(temporary))
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    ROOT / "scripts/manager_dev.py",
                    "doctor",
                    "--project",
                    workspace,
                ],
                capture_output=True,
                text=True,
                env={**os.environ, "PATH": ""},
            )
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["ready"])
        self.assertIsNone(report["tools"]["uv"])

    def test_package_install_dry_run_selects_product_without_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary) / "unused-store"
            workspace = self.fixture_workspace(Path(temporary))
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    ROOT / "agent-manager/dev.py",
                    "install",
                    "--dry-run",
                    "--prefix",
                    prefix,
                    "--project",
                    workspace,
                ],
                cwd=temporary,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["schema"], 2)
            self.assertEqual(report["selection"], "manager-suite/agent-manager")
            self.assertFalse(report["executed"])
            self.assertFalse(prefix.exists())


if __name__ == "__main__":
    unittest.main()
