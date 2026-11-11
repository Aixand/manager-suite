"""The installed CLI must not contain modules removed from the source tree."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace


class PackageSourceTests(unittest.TestCase):
    def test_snapshot_excludes_stale_build_outputs_and_keeps_package_data(self):
        script = Path(__file__).resolve().parents[1] / "dev.py"
        spec = importlib.util.spec_from_file_location(
            "agent_cli_install_fixture", script
        )
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "project"
            source.mkdir()
            for name in ("pyproject.toml", "README.md", "LICENSE"):
                (source / name).write_text(name)
            for relative in (
                "agent_manager/__init__.py",
                "agent_manager/providers/definitions/codex.toml",
                "agent_manager/__pycache__/removed.pyc",
                "build/lib/agent_manager/resources/__init__.py",
                "agent_manager.egg-info/SOURCES.txt",
            ):
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative)

            def inspect_build(command, checkout):
                destination = Path(command[-1])
                self.assertTrue(
                    (
                        destination / "agent_manager/providers/definitions/codex.toml"
                    ).is_file()
                )
                self.assertFalse((destination / "build").exists())
                self.assertFalse((destination / "agent_manager/resources").exists())
                self.assertFalse((destination / "agent_manager/__pycache__").exists())
                self.assertFalse((destination / "agent_manager.egg-info").exists())

            with patch.object(installer, "run", side_effect=inspect_build):
                installer.build(
                    source,
                    SimpleNamespace(output=root / "output", python="3.11"),
                    "uv",
                    root / "python",
                )
