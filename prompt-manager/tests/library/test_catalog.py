import concurrent.futures
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_manager import Manager, PromptError


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.packs = self.root / "packs"
        shutil.copytree(Path(__file__).parents[2] / "packs", self.packs)
        self.manager = Manager(self.root / "store", packs=[])
        self.manager.register_source("development", [self.packs])
        self.manager.release(self.packs / "common", "1")

    def draft(self, name="agents"):
        return next(
            row
            for row in self.manager.catalog()["packs"]
            if row["kind"] == "draft" and row["name"] == name
        )

    def test_content_edit_is_detected_without_registration_or_version_change(self):
        before = self.draft()
        file = self.packs / "agents/sections/review.md"
        file.write_text(file.read_text() + "\nCheck concurrency.\n")
        after = self.draft()
        self.assertEqual(before["id"], after["id"])
        self.assertNotEqual(before["digest"], after["digest"])
        self.assertEqual(before["version"], after["version"])

    def test_catalog_reads_do_not_create_storage(self):
        home = self.root / "uncreated"
        Manager(home, packs=[self.packs / "common"]).catalog()
        self.assertFalse(home.exists())

    def test_errors_are_isolated_and_duplicate_pack_names_have_distinct_ids(self):
        duplicate = self.root / "other"
        shutil.copytree(self.packs / "agents", duplicate)
        self.manager.register_source("other", [duplicate])
        self.manager.register_source("missing", [self.root / "missing"])
        (self.manager.store.home / "sources/broken.json").write_text("[]")
        (self.manager.store.home / "releases/common/broken.json").write_text("{}")
        result = self.manager.catalog()
        agents = [
            row for row in result["packs"] if row["kind"] == "draft" and row["name"] == "agents"
        ]
        self.assertEqual(len(agents), 2)
        self.assertNotEqual(agents[0]["id"], agents[1]["id"])
        self.assertTrue(any(row.get("error") for row in result["sources"]))
        self.assertTrue(any(row["status"] == "invalid" for row in result["packs"]))
        self.assertEqual(
            self.manager.show_catalog(agents[0]["id"])["files"]["sections/review.md"],
            (self.packs / "agents/sections/review.md").read_text(),
        )

    def test_unreleased_dependencies_allow_source_inspection(self):
        manager = Manager(self.root / "empty-store", packs=[self.packs])
        row = next(row for row in manager.catalog()["packs"] if row["name"] == "agents")
        self.assertEqual(row["status"], "invalid")
        self.assertEqual(len(row["prompts"]), 3)
        shown = manager.show_catalog(row["id"], prompt="review")
        self.assertIn("sections/review.md", shown["files"])
        self.assertIsNone(shown["release"]["digest"])

    def test_concurrent_saves_have_one_winner_and_releases_preserve_original_content(self):
        row = self.draft()
        old = self.manager.release_catalog(row["id"], "1", expected_digest=row["digest"])
        original = self.manager.show("agents@1/review")["files"]["sections/review.md"]

        def save(suffix):
            try:
                self.manager.save_catalog_file(
                    row["id"],
                    "sections/review.md",
                    original + suffix,
                    expected_digest=row["digest"],
                )
                return "saved"
            except PromptError as error:
                return error.code

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, ["\nFirst edit", "\nSecond edit"]))
        self.assertCountEqual(results, ["saved", "conflict"])
        self.assertEqual(
            self.manager.show("agents@1/review")["files"]["sections/review.md"], original
        )
        with self.assertRaises(PromptError) as error:
            self.manager.release_catalog(row["id"], "2", expected_digest=old["digest"])
        self.assertEqual(error.exception.code, "conflict")

    def test_invalid_or_outside_asset_edits_do_not_change_source(self):
        row = self.draft()
        original = (self.packs / "agents/pack.json").read_bytes()
        for file, text in [
            ("../outside.md", "outside"),
            ("pack.json", "{}"),
            ("sections/review.md", "{{ undeclared }}"),
        ]:
            with self.assertRaises(PromptError):
                self.manager.save_catalog_file(row["id"], file, text, expected_digest=row["digest"])
        self.assertEqual((self.packs / "agents/pack.json").read_bytes(), original)
        self.assertEqual(self.draft()["digest"], row["digest"])

    def test_entry_operations_do_not_resolve_unrelated_application_or_managed_content(self):
        row = self.draft()
        self.manager.register_application(
            "unrelated",
            prompts={
                "review": {
                    "kind": "managed",
                    "publisher_id": "absent",
                    "package_id": "default",
                    "prompt": "review",
                },
            },
        )
        with (
            patch(
                "prompt_manager.library.catalog.capture",
                side_effect=AssertionError("Application resolution"),
            ) as applications,
            patch(
                "prompt_manager.library.catalog.managed_catalog",
                side_effect=AssertionError("Managed inspection"),
            ) as managed,
        ):
            entry, path = self.manager.catalog_source(row["id"])
            self.assertEqual(row["digest"], entry["digest"])
            self.assertEqual(self.packs / "agents", path)
            shown = self.manager.show_catalog(row["id"])
            saved = self.manager.save_catalog_file(
                row["id"],
                "sections/review.md",
                shown["files"]["sections/review.md"],
                expected_digest=row["digest"],
            )
            published = self.manager.release_catalog(
                row["id"],
                "isolated",
                expected_digest=saved["entry"]["digest"],
            )
            self.assertEqual(row["digest"], published["digest"])
            applications.assert_not_called()
            managed.assert_not_called()

    def test_draft_save_syncs_directory_after_replacing_the_file(self):
        row = self.draft()
        file = "sections/review.md"
        updated = "Updated instructions."

        def after_replace(directory):
            self.assertEqual(self.packs / "agents/sections", directory)
            self.assertEqual(updated, (directory / "review.md").read_text())

        with patch(
            "prompt_manager.library.authoring.sync_directory", side_effect=after_replace
        ) as sync:
            self.manager.save_catalog_file(row["id"], file, updated, expected_digest=row["digest"])
        sync.assert_called_once()

    def test_store_identity(self):
        other = Manager(self.root / "second-store", packs=[self.packs / "common"])
        first = self.draft("common")
        second = other.catalog()["packs"][0]
        self.assertNotEqual(first["id"], second["id"])


if __name__ == "__main__":
    unittest.main()
