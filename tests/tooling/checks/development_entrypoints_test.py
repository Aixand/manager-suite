"""Package helpers work without parent scripts and keep portable development locks."""

from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import suite


class DevelopmentEntrypoints(unittest.TestCase):
    def test_doctor_rejects_missing_uv_and_policy_drift(self):
        with (
            patch("suite.shutil.which", return_value=None),
            patch(
                "provider_policy.synchronize", side_effect=ValueError("stale policy")
            ),
        ):
            result = suite.doctor()
        self.assertFalse(result["ready"])
        self.assertFalse(result["checks"]["provider-policy"])
        self.assertFalse(result["checks"]["python:3.12.14"])

    def test_release_wheel_ignores_cached_modules_and_preserves_assets(self):
        import product_payload

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "package"
            (root / "build/lib/example").mkdir(parents=True)
            (root / "build/lib/example/retired.py").write_text("retired = True")
            (root / "example").mkdir()
            (root / "example/current.py").write_text("current = True")
            (root / "example/.settings").write_text("asset")
            (root / ".env").write_text("private")
            for name in (".workflow-tests", ".workflow-recovery", ".prompt-manager"):
                (root / name).mkdir()
                (root / name / "local-state").write_text("private local fixture state")
            output = Path(temporary) / "output"

            def inspect_build(*command):
                snapshot = Path(command[-1])
                self.assertFalse((snapshot / "build").exists())
                self.assertFalse((snapshot / ".env").exists())
                for name in (
                    ".workflow-tests",
                    ".workflow-recovery",
                    ".prompt-manager",
                ):
                    self.assertFalse((snapshot / name).exists())
                self.assertTrue((snapshot / "example/current.py").is_file())
                self.assertEqual((snapshot / "example/.settings").read_text(), "asset")

            with (
                patch("provider_policy.synchronize"),
                patch.object(product_payload, "run", side_effect=inspect_build),
                patch.object(product_payload, "notices", return_value=[]),
            ):
                product_payload.wheel(output, root)
            self.assertTrue((root / "build/lib/example/retired.py").exists())

    def test_build_preserves_hidden_example_data_and_omits_local_environment(self):
        spec = importlib.util.spec_from_file_location(
            "package_dev", ROOT / "scripts/development/python_dev.py"
        )
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            example = root / "examples/sample/.audit-settings"
            example.parent.mkdir(parents=True)
            example.write_bytes(b"format=csv\n")
            (root / ".env").write_text("private configuration")
            (root / ".venv").mkdir()
            (root / ".venv/state").write_text("local environment")

            def inspect_snapshot(command, checkout):
                snapshot = Path(command[-1])
                self.assertEqual(
                    (snapshot / "examples/sample/.audit-settings").read_bytes(),
                    b"format=csv\n",
                )
                self.assertFalse((snapshot / ".env").exists())
                self.assertFalse((snapshot / ".venv").exists())

            with patch.object(helper, "run", side_effect=inspect_snapshot):
                helper.build(
                    root,
                    SimpleNamespace(output=root / "dist", python="3.11"),
                    "uv",
                    root / ".venv/bin/python",
                )

    def test_all_package_entrypoints_work_in_an_unrelated_directory(self):
        canonical = (ROOT / "scripts/development/python_dev.py").read_bytes()
        for name in suite.COMPONENTS:
            with self.subTest(package=name), tempfile.TemporaryDirectory() as temporary:
                package = ROOT / name
                self.assertEqual((package / "dev.py").read_bytes(), canonical)
                selected = Path(temporary) / "package with spaces"
                selected.mkdir()
                for filename in ("dev.py", "dev.sh", "dev.ps1"):
                    shutil.copy2(package / filename, selected / filename)
                commands = [
                    [sys.executable, "-I", "-S", str(selected / "dev.py"), "help"]
                ]
                if os.name != "nt":
                    commands.append(["sh", str(selected / "dev.sh"), "help"])
                for command in commands:
                    result = subprocess.run(
                        command,
                        cwd=temporary,
                        capture_output=True,
                        text=True,
                        env={**os.environ, "AIXAND_DEV_PYTHON": sys.executable},
                        timeout=15,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("develop", result.stdout)
                    self.assertIn("build", result.stdout)

    def test_locks_are_versioned_and_local_sources_match_the_declared_identity(self):
        for name in suite.COMPONENTS:
            with self.subTest(package=name):
                package = ROOT / name
                config = tomllib.loads((package / "development.toml").read_text())
                lock = (package / "requirements-dev.lock").read_text()
                self.assertNotIn(str(ROOT), lock)
                self.assertNotIn("file://", lock)
                self.assertNotIn("\n-e ", lock)
                for dependency, relative in config.get("sources", {}).items():
                    project = tomllib.loads(
                        (package / relative / "pyproject.toml").read_text()
                    )["project"]
                    self.assertEqual(dependency, project["name"])
                    self.assertIn(f"{dependency}=={project['version']}\n", lock)
                for filename in (
                    "README.md",
                    "docs/development.md",
                    "MANIFEST.in",
                    ".python-version",
                ):
                    self.assertTrue((package / filename).is_file(), filename)


if __name__ == "__main__":
    unittest.main()
