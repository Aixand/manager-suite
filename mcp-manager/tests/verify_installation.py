"""Verify an offline wheelhouse without the checkout or retired source framework.

Usage: python tests/verify_installation.py --wheelhouse /path/to/three/wheels
The wheelhouse must contain MCP Manager, filesystem and GitHub Source wheels.
"""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def verify(wheelhouse):
    packages = list(wheelhouse.glob("aixand_mcp_manager-*.whl"))
    if len(packages) != 1:
        raise ValueError("Provide a wheelhouse containing one MCP Manager wheel")
    with tempfile.TemporaryDirectory(prefix="mcp-installed-proof-") as directory:
        root = Path(directory)
        environment = root / "environment"
        subprocess.run(
            [sys.executable, "-m", "venv", "--without-pip", str(environment)],
            check=True,
        )
        python = environment / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "--python",
                str(python),
                "install",
                "--no-index",
                "--find-links",
                str(wheelhouse),
                str(packages[0]),
            ],
            check=True,
        )
        installed = Path(
            subprocess.check_output(
                [
                    str(python),
                    "-I",
                    "-c",
                    'import sysconfig; print(sysconfig.get_path("purelib"))',
                ],
                text=True,
            ).strip()
        )
        # Site customization is inherited by native source/preparation workers
        # using this interpreter, so hidden dependencies fail in children too.
        (installed / "sitecustomize.py").write_text(
            """
import importlib.abc, sys
class Boundary(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'resource_sources', 'agent_manager', 'prompt_manager',
                'skill_manager', 'conversation_manager', 'server_manager', 'runner', 'carrier', 'suite'}:
            raise ModuleNotFoundError('Standalone boundary rejected: ' + fullname)
sys.meta_path.insert(0, Boundary())
""",
            encoding="utf-8",
        )
        tests = root / "tests"
        tests.mkdir()
        for name in (
            "test_installations.py",
            "test_source_migration.py",
            "test_sources.py",
            "source_fixture.py",
        ):
            shutil.copy2(ROOT / "tests" / name, tests / name)
        shutil.copytree(
            ROOT / "tests/fixtures",
            tests / "fixtures",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in ("PYTHONPATH", "PYTHONHOME")
        }
        env.update(
            PIP_NO_INDEX="1", npm_config_offline="true", PYTHONDONTWRITEBYTECODE="1"
        )
        subprocess.run(
            [
                str(python),
                "-I",
                "-c",
                """
import importlib.metadata, sys
from mcp_manager import Manager
assert {item.metadata['Name'].lower().replace('_', '-') for item in importlib.metadata.distributions()} == {
    'aixand-mcp-manager', 'aixand-lib-filesystem', 'aixand-lib-github-source'}
assert 'resource_sources' not in sys.modules
assert Manager(sys.argv[1]).catalog() == []
""",
                str(root / "empty-store"),
            ],
            cwd=root,
            env=env,
            check=True,
        )
        subprocess.run(
            [str(python), "-I", "-m", "unittest", "discover", "-s", str(tests), "-v"],
            cwd=root,
            env=env,
            check=True,
            timeout=180,
        )
        command = environment / (
            "Scripts/mcp-manager.exe" if os.name == "nt" else "bin/mcp-manager"
        )
        result = subprocess.run(
            [
                str(command),
                "--home",
                str(root / "empty-store"),
                "managed",
                "list",
                "--json",
            ],
            cwd=root,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.stdout.strip() == "[]", result.stdout
    print(
        "Installed lifecycle verified with only MCP Manager and its two utility dependencies"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    verify(parser.parse_args().wheelhouse.resolve())
