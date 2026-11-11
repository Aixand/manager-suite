from pathlib import Path
import shutil
import tempfile
import unittest

from skill_manager import Manager


class ExampleTests(unittest.TestCase):
    def test_realistic_example_release_and_fake_consumer(self):
        with tempfile.TemporaryDirectory(prefix="skill-example-") as temporary:
            root = Path(temporary)
            source = root / "source"
            example = Path(__file__).resolve().parents[1] / "examples" / "csv-audit"
            shutil.copytree(example, source / "csv-audit")
            # Git and source archives do not retain empty directories. Construct
            # this fixture explicitly so the test also covers a clean checkout.
            (source / "csv-audit" / "assets" / "empty").mkdir(exist_ok=True)
            manager = Manager(root / "store")
            manager.register_source("example", [source])
            entry = manager.catalog()["entries"][0]
            self.assertTrue(manager.validate(entry["id"])["valid"])
            release = manager.release(entry["id"], "v1", expected_digest=entry["digest"])
            exported = manager.export(release["reference"], root / "csv-audit")
            self.assertEqual(release["digest"], exported["digest"])
            for relative in ("SKILL.md", "scripts/audit.py", "references/rules.md", "assets/sample.csv", "assets/badge.png", ".audit-settings"):
                self.assertEqual((example / relative).read_bytes(), (root / "csv-audit" / relative).read_bytes())
            self.assertTrue((root / "csv-audit" / "assets" / "empty").is_dir())
            self.assertTrue((root / "csv-audit" / "scripts" / "audit.py").stat().st_mode & 0o111)


if __name__ == "__main__":
    unittest.main()
