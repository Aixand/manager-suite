"""Keep release profiles, dependency sources, and package identities aligned."""

from pathlib import Path
import re
import sys
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import manager_profiles
import suite


def read(path):
    return tomllib.loads(path.read_text())


class DistributionNamesTests(unittest.TestCase):
    def test_product_distributions_match_metadata_and_use_aixand_prefix(self):
        projects = {
            read(ROOT / path / "pyproject.toml")["project"]["name"]
            for path in suite.COMPONENTS
        }
        for product in manager_profiles.PROFILES:
            distribution, _ = manager_profiles.PACKAGES[product]
            with self.subTest(product=product):
                self.assertTrue(distribution.startswith("aixand-"))
                self.assertIn(distribution, projects)

    def test_local_sources_match_target_distribution(self):
        for directory in [ROOT, ROOT / "installer", *[ROOT / p for p in suite.COMPONENTS]]:
            metadata = read(directory / "pyproject.toml")
            for name, source in metadata.get("tool", {}).get("uv", {}).get("sources", {}).items():
                if "path" not in source:
                    continue
                target = directory / source["path"] / "pyproject.toml"
                if target.exists():
                    with self.subTest(project=str(directory), dependency=name):
                        self.assertEqual(name, read(target)["project"]["name"])

    def test_installer_extras_use_distribution_names(self):
        manifest = read(ROOT / "installation.toml")
        for product, extras in manager_profiles.EXTRAS.items():
            distribution, _ = manager_profiles.PACKAGES[product]
            self.assertEqual(
                manifest["products"][f"manager-suite/{product}"]["extras"],
                {distribution: extras},
            )

    def test_dependency_requirements_do_not_use_retired_names(self):
        for path in suite.COMPONENTS:
            project = read(ROOT / path / "pyproject.toml")["project"]
            dependencies = list(project.get("dependencies", []))
            for group in project.get("optional-dependencies", {}).values():
                dependencies.extend(group)
            for dependency in dependencies:
                name = re.split(r"[\[<>=!~; ]", dependency, maxsplit=1)[0]
                with self.subTest(project=path, dependency=dependency):
                    self.assertNotIn(name, {"agent-manager", "orchestration-mcp"})


if __name__ == "__main__":
    unittest.main()
