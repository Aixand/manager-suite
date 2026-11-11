"""Manager builds and installs use their declared source dependencies."""

import ast
from pathlib import Path
import sys
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import manager_profiles
import suite


class SourceDependencyBoundaryTests(unittest.TestCase):
    def test_profiles_manifests_and_lock_select_shared_utilities(self):
        self.assertFalse((ROOT / "resource-sources").exists())
        self.assertNotIn("resource-sources", manager_profiles.PACKAGES)
        self.assertNotIn("resource-sources", suite.COMPONENTS)
        self.assertNotIn("../aixand-libraries/filesystem", suite.COMPONENTS)
        self.assertNotIn("../aixand-libraries/github-source", suite.COMPONENTS)
        self.assertEqual(
            manager_profiles.PROFILES["mcp-manager"],
            ["filesystem", "github-source", "mcp-manager"],
        )
        manifest = tomllib.loads((ROOT / "installation.toml").read_text())
        components = manifest["components"]
        self.assertNotIn("resource-sources", components)
        self.assertEqual(components["mcp-manager"]["dependencies"], [])
        for record in components.values():
            for dependency in record.get("dependencies", []):
                self.assertIn(dependency, components)
        for name in (
            "installation.toml",
            "installer/pyproject.toml",
            "installer/uv.lock",
            "scripts/build_installers.py",
        ):
            self.assertNotIn("resource-sources", (ROOT / name).read_text(), name)
            self.assertNotIn("resource_sources", (ROOT / name).read_text(), name)

    def test_managers_use_declared_source_dependencies(self):
        for directory in (
            "mcp-manager/src",
            "prompt-manager/src",
            "skill-manager/src",
            "agent-manager/agent_manager",
            "conversation-manager/src",
        ):
            for path in (ROOT / directory).rglob("*.py"):
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                    names = (
                        [item.name for item in node.names]
                        if isinstance(node, ast.Import)
                        else [node.module or ""]
                        if isinstance(node, ast.ImportFrom)
                        else []
                    )
                    self.assertFalse(
                        any(name.split(".")[0] == "resource_sources" for name in names),
                        path,
                    )
        for component in suite.COMPONENTS:
            metadata = tomllib.loads((ROOT / component / "pyproject.toml").read_text())
            self.assertFalse(
                any(
                    "resource-sources" in dependency
                    for dependency in metadata["project"].get("dependencies", [])
                ),
                component,
            )


if __name__ == "__main__":
    unittest.main()
