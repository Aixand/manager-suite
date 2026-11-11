import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from support import managed_lifecycle

from prompt_manager import Manager, PromptError


class ManagedPromptTests(managed_lifecycle.LifecycleTests):
    error = PromptError

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home, self.source = self.root / "store", self.root / "sample"
        self.source.mkdir()
        manifest = {
            "schema": "prompt-manager/pack-1",
            "name": "sample",
            "sections": {"body": {"file": "body.md"}, "notes": {"file": "notes.md"}},
            "prompts": {
                "main": {
                    "sections": [
                        {"id": "body", "section": "body"},
                        {"id": "notes", "section": "notes"},
                    ]
                }
            },
        }
        (self.source / "pack.json").write_text(json.dumps(manifest))
        self.write_source("body.md", "first\nmiddle\nlast\n")
        self.write_source("notes.md", "notes\n")
        self.manager = Manager(self.home, packs=[])

    def ensure(self, revision):
        return self.manager.ensure_registered(
            self.publisher, self.source, package_id=self.identifier, publisher_revision=revision
        )

    def write_source(self, path, text):
        (self.source / path).write_text(text)

    def invalidate_source(self):
        self.write_source("pack.json", "{invalid")

    def file_changes(self, changes):
        return changes

    def content(self, *, revision=None):
        return self.manager.resolve_managed(self.publisher, self.identifier, revision=revision)[
            "snapshot"
        ]["files"]["body.md"]

    def test_sdk_renders_owned_copy_and_reports_pending_update(self):
        app = self.manager.application(
            self.publisher,
            bundled_pack=self.source,
            package_id=self.identifier,
            publisher_revision=1,
        )
        self.save("user\n")
        self.write_source("body.md", "publisher\n")
        app.publisher_revision = 2
        app.refresh()
        result = app.render("main")
        self.assertIn("user", result["output"])
        self.assertNotIn("publisher", result["output"])
        self.assertTrue(result["resolution"]["reconciliation_required"])
        shutil.rmtree(self.source)
        restarted = self.manager.application(
            self.publisher,
            bundled_pack=self.source,
            package_id=self.identifier,
            publisher_revision=2,
        )
        self.assertIn("user", restarted.render("main")["output"])
        self.assertIsNotNone(restarted.status()["sync_error"])

    def test_cli_lifecycle(self):
        def cli(*args):
            process = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "prompt_manager",
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

        registered = cli(
            "ensure", self.publisher, self.identifier, str(self.source), "--publisher-revision", "1"
        )
        rendered = cli("render", self.publisher, self.identifier, "main")
        self.assertIn("first", rendered["output"])
        self.assertEqual(
            registered["revision"], cli("status", self.publisher, self.identifier)["revision"]
        )
        self.assertEqual(1, len(cli("list")))

    def test_atomic_multifile_edit_validates_manifest_and_does_not_write_source(self):
        self.ensure(1)
        original = (self.source / "body.md").read_text()
        with self.assertRaises(PromptError):
            self.manager.save_managed(
                self.publisher,
                self.identifier,
                {"pack.json": "{}"},
                expected_revision=self.status()["revision"],
            )
        self.save("owned edit\n")
        self.assertEqual(original, (self.source / "body.md").read_text())
        self.assertIn("managed", self.manager.catalog())

    def test_existing_application_can_select_owned_content_and_pin_a_revision(self):
        initial = self.ensure(1)
        application = self.manager.register_application(
            "consumer",
            prompts={
                "review": {
                    "kind": "managed",
                    "publisher_id": self.publisher,
                    "package_id": self.identifier,
                    "prompt": "main",
                }
            },
        )
        self.save("user\n")
        self.write_source("body.md", "publisher\n")
        self.ensure(2)
        result = self.manager.render_application("consumer", "review")
        self.assertIn("user", result["output"])
        self.assertTrue(result["resolution"]["managed"]["reconciliation_required"])
        self.manager.set_application_prompt(
            "consumer",
            "review",
            {
                "kind": "managed",
                "publisher_id": self.publisher,
                "package_id": self.identifier,
                "prompt": "main",
                "revision": initial["revision"],
            },
            expected_revision=application["revision"],
        )
        self.assertIn("first", self.manager.render_application("consumer", "review")["output"])

    def test_owned_copy_can_publish_existing_release_contract(self):
        initial = self.ensure(1)
        self.save("user\n")
        released = self.manager.release_managed(self.publisher, self.identifier, "v1")
        self.assertIn("user", self.manager.render("sample@v1/main")["output"])
        self.assertEqual(released["digest"], self.status()["active_digest"])
        self.manager.release_managed(
            self.publisher, self.identifier, "original", revision=initial["revision"]
        )
        self.assertIn("first", self.manager.render("sample@original/main")["output"])
        with self.assertRaises(PromptError):
            self.manager.release_managed(
                self.publisher, self.identifier, "v1", revision=initial["revision"]
            )

    def test_large_structured_conflicts_are_paged_and_bounded(self):
        manifest = json.loads((self.source / "pack.json").read_text())
        manifest["prompts"]["main"]["inputs"] = {
            f"field{i}": {"type": "string", "default": "😀" * 2000} for i in range(20)
        }
        (self.source / "pack.json").write_text(json.dumps(manifest))
        self.ensure(1)
        for field in manifest["prompts"]["main"]["inputs"].values():
            field["default"] += " user"
        self.save(json.dumps(manifest), file="pack.json")
        for field in manifest["prompts"]["main"]["inputs"].values():
            field["default"] += " publisher"
        (self.source / "pack.json").write_text(json.dumps(manifest))
        self.ensure(2)
        offset, paths = 0, []
        while offset is not None:
            view = self.manager.managed_view(
                self.publisher, self.identifier, review=True, path="/pack", conflict_offset=offset
            )
            self.assertLessEqual(len(view["selected_conflicts"]), 8)
            self.assertLess(len(json.dumps(view)), 512 * 1024)
            paths.extend(row["path"] for row in view["selected_conflicts"])
            offset = view["next_conflict_offset"]
        self.assertEqual(20, len(set(paths)))
