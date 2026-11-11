"""Build from disposable sources so cached/deleted modules cannot enter releases."""

from contextlib import contextmanager
from pathlib import Path
import shutil
import tempfile


@contextmanager
def source_snapshot(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()

    def ignored(directory, names):
        excluded = {
            "build",
            "dist",
            "venv",
            "__pycache__",
            "AGENTS.md",
            ".git",
            ".hg",
            ".svn",
            ".venv",
            ".venv-hooks",
            ".ruff_cache",
            ".pytest_cache",
            ".mypy_cache",
            ".tox",
            ".nox",
            ".workflow-tests",
            ".workflow-recovery",
            ".prompt-manager",
        }
        return [
            name
            for name in names
            if name in excluded
            or name.endswith(".egg-info")
            or (name.startswith(".env") and name != ".env.example")
            or name.endswith((".pyc", ".pyo"))
            or (Path(directory) / name).resolve() == output
        ]

    with tempfile.TemporaryDirectory(prefix="manager-source-") as temporary:
        snapshot = Path(temporary) / "source"
        shutil.copytree(source, snapshot, ignore=ignored)
        yield snapshot
