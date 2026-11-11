import base64
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import managed_lifecycle
from skill_manager import Manager, SkillError


class ManagedSkillTests(managed_lifecycle.LifecycleTests):
    error = SkillError

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home, self.source = self.root / "store", self.root / "sample"
        self.source.mkdir()
        self.write_source(
            "SKILL.md",
            "---\nname: sample\ndescription: Inspect sample data.\n---\nUse body.md.\n",
        )
        self.write_source("body.md", "first\nmiddle\nlast\n")
        self.write_source("notes.md", "notes\n")
        (self.source / "binary.dat").write_bytes(b"\x00\xff")
        (self.source / "empty").mkdir()
        self.manager = Manager(self.home)

    def ensure(self, revision):
        return self.manager.ensure_registered(
            self.publisher,
            self.source,
            skill_id=self.identifier,
            publisher_revision=revision,
        )

    def write_source(self, path, text):
        (self.source / path).write_text(text)

    def invalidate_source(self):
        self.write_source("SKILL.md", "invalid metadata")

    def file_changes(self, changes):
        return {
            path: None
            if text is None
            else {"content": text.encode(), "executable": False}
            for path, text in changes.items()
        }

    def content(self, *, revision=None):
        return self.manager.read_managed_file(
            self.publisher, self.identifier, "body.md", revision=revision
        ).decode()

    def test_sdk_exports_full_owned_package_after_source_removal(self):
        app = self.manager.application(
            self.publisher,
            bundled_skills={self.identifier: self.source},
            publisher_revision=1,
        )
        self.save("owned edit\n")
        self.assertEqual("first\nmiddle\nlast\n", (self.source / "body.md").read_text())
        shutil.rmtree(self.source)
        restarted = self.manager.application(
            self.publisher,
            bundled_skills={self.identifier: self.source},
            publisher_revision=1,
        )
        destination = self.root / "export" / "sample"
        destination.parent.mkdir()
        receipt = restarted.export(self.identifier, destination)
        self.assertEqual("exported", receipt["status"])
        self.assertEqual("owned edit\n", (destination / "body.md").read_text())
        self.assertEqual(b"\x00\xff", (destination / "binary.dat").read_bytes())
        self.assertTrue((destination / "empty").is_dir())
        self.assertIsNotNone(app.status()[0]["sync_error"])

    def test_binary_asset_conflicts_and_executable_changes(self):
        self.ensure(1)
        self.manager.save_managed(
            self.publisher,
            self.identifier,
            {"binary.dat": {"content": b"\x00\x01", "executable": True}},
            expected_revision=self.status()["revision"],
        )
        (self.source / "binary.dat").write_bytes(b"\x00\x02")
        pending = self.ensure(2)
        preview = self.manager.preview_reconciliation(self.publisher, self.identifier)
        path = preview["conflicts"][0]["path"]
        self.assertTrue(path.endswith("/content_b64"))
        self.manager.reconcile(
            self.publisher,
            self.identifier,
            expected_revision=pending["revision"],
            resolutions={path: {"take": "publisher"}},
        )
        self.assertEqual(
            b"\x00\x02",
            self.manager.read_managed_file(
                self.publisher, self.identifier, "binary.dat"
            ),
        )
        row = next(
            row
            for row in self.manager.resolve_managed(self.publisher, self.identifier)[
                "manifest"
            ]["files"]
            if row["path"] == "binary.dat"
        )
        self.assertTrue(row["executable"])

    def test_cli_owned_import_read_and_save(self):
        def cli(*args):
            process = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "skill_manager",
                    "--home",
                    str(self.home),
                    "managed",
                    *args,
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, process.returncode, process.stderr)
            return json.loads(process.stdout)

        row = cli(
            "ensure",
            self.publisher,
            self.identifier,
            str(self.source),
            "--publisher-revision",
            "1",
        )
        files = json.dumps(
            {"body.md": {"content_b64": base64.b64encode(b"CLI edit\n").decode()}}
        )
        cli(
            "save",
            self.publisher,
            self.identifier,
            "--files",
            files,
            "--expected-revision",
            row["revision"],
        )
        result = cli("read", self.publisher, self.identifier, "body.md")
        self.assertEqual(b"CLI edit\n", base64.b64decode(result["content_b64"]))

    def test_owned_release_works_with_existing_resolution_and_export(self):
        initial = self.ensure(1)
        self.save("user\n")
        release = self.manager.release_managed(self.publisher, self.identifier, "v1")
        resolved = self.manager.resolve(release["reference"])
        self.assertEqual(self.status()["active_digest"], resolved["digest"])
        self.assertEqual(
            b"user\n", self.manager.read_file(release["reference"], "body.md")
        )
        original = self.manager.release_managed(
            self.publisher, self.identifier, "original", revision=initial["revision"]
        )
        self.assertEqual(
            b"first\nmiddle\nlast\n",
            self.manager.read_file(original["reference"], "body.md"),
        )
        with self.assertRaises(SkillError):
            self.manager.release_managed(
                self.publisher, self.identifier, "v1", revision=initial["revision"]
            )
