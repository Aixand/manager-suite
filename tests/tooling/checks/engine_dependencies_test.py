"""Product installs and development builds select the conversation engine."""

import ast
from pathlib import Path
import sys
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import manager_profiles
import suite


class EngineDependencyBoundaryTests(unittest.TestCase):
    def test_product_manifests_and_profiles_select_only_current_engine(self):
        self.assertFalse((ROOT / "server-manager").exists())
        self.assertNotIn("server-manager", manager_profiles.PACKAGES)
        self.assertNotIn("server-manager", suite.COMPONENTS)
        self.assertIn("conversation-manager", suite.COMPONENTS)
        manifest = tomllib.loads((ROOT / "installation.toml").read_text())
        self.assertIn("manager-suite/conversation-manager", manifest["products"])
        self.assertNotIn("manager-suite/server-manager", manifest["products"])
        components = manifest["components"]

        def visit(name, ancestors):
            self.assertNotIn(name, ancestors, "Component dependency cycle")
            for dependency in components[name].get("dependencies", []):
                self.assertIn(dependency, components)
                visit(dependency, ancestors | {name})

        for name in components:
            visit(name, set())
        for file in [
            "installation.toml",
            "installer/pyproject.toml",
            "installer/uv.lock",
        ]:
            self.assertNotIn("server-manager", (ROOT / file).read_text(), file)

    def test_engines_use_declared_dependencies(self):
        for directory in [
            "agent-manager/agent_manager",
            "conversation-manager/src/conversation_manager",
        ]:
            for file in (ROOT / directory).rglob("*.py"):
                for node in ast.walk(ast.parse(file.read_text())):
                    names = (
                        [v.name for v in node.names]
                        if isinstance(node, ast.Import)
                        else [node.module or ""]
                        if isinstance(node, ast.ImportFrom)
                        else []
                    )
                    self.assertFalse(
                        any(name.split(".")[0] == "server_manager" for name in names),
                        str(file),
                    )


if __name__ == "__main__":
    unittest.main()
