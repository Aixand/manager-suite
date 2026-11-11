"""Independent copies and reversible organization preserve prompt references."""

import concurrent.futures
import inspect
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_manager import Manager, Package, PromptError
from prompt_manager.codec import digest
from prompt_manager.managed.registration import Backend
from prompt_manager.storage.files import atomic_json


class OrganizationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manager = Manager(self.root / "store", packs=[])
        self.manager.create_managed("review", content="Original.")
        self.package = self.manager.package("local", "review")
        self.initial = self.package.status()

    def source(self):
        source = self.root / "publisher"
        source.mkdir()
        snapshot = self.package.resolve()["snapshot"]
        (source / "pack.json").write_text(json.dumps(snapshot["pack"]))
        (source / "body.md").write_text("Publisher one.")
        return source

    def files(self):
        return {str(path): path.read_bytes() for path in self.manager.store.home.rglob("*.json")}

    def test_organization_methods_require_keyword_revision_guards(self):
        for method in (Package.rename, Package.archive, Package.unarchive):
            guard = inspect.signature(method).parameters["expected_revision"]
            self.assertEqual(inspect.Parameter.KEYWORD_ONLY, guard.kind)
            self.assertIs(inspect.Parameter.empty, guard.default)

    def test_display_rename_preserves_identity_content_mappings_and_release_pins(self):
        self.package.release("1")
        app = self.manager.register_application(
            "workspace",
            prompts={
                "review": {
                    "kind": "managed",
                    "publisher_id": "local",
                    "package_id": "review",
                    "prompt": "review",
                }
            },
        )
        before = self.package.render("review")["output"]
        renamed = self.package.rename(
            "My review instructions", expected_revision=self.initial["revision"]
        )
        self.assertEqual("review", renamed["id"])
        self.assertEqual("My review instructions", renamed["label"])
        self.assertEqual(self.initial["active_digest"], renamed["active_digest"])
        self.assertNotEqual(self.initial["revision"], renamed["revision"])
        self.assertEqual(before, self.package.render("review")["output"])
        self.assertEqual(
            before, self.package.render("review", revision=self.initial["revision"])["output"]
        )
        self.assertEqual(before, self.manager.render("review@1/review")["output"])
        self.assertEqual(before, self.manager.render_application("workspace", "review")["output"])
        self.assertEqual(app, self.manager.application_descriptor("workspace"))
        self.assertEqual("My review instructions", self.package.inspect()["label"])
        self.assertEqual("review", self.package.resolve()["snapshot"]["pack"]["name"])
        self.assertEqual(
            renamed["revision"],
            self.package.rename("My review instructions", expected_revision=renamed["revision"])[
                "revision"
            ],
        )
        cleared = self.package.rename(None, expected_revision=renamed["revision"])
        self.assertNotIn("label", cleared)
        self.assertEqual("review", self.package.inspect()["label"])

    def test_invalid_names_and_stale_metadata_writes_do_not_change_records(self):
        before = self.files()
        for invalid in ("", "  ", "x" * 129, 42, "\ud800"):
            with self.subTest(value=repr(invalid)), self.assertRaises(PromptError):
                self.package.rename(invalid, expected_revision=self.initial["revision"])
        self.assertEqual(before, self.files())
        renamed = self.package.rename("Updated", expected_revision=self.initial["revision"])
        with self.assertRaises(PromptError) as error:
            self.package.archive(expected_revision=self.initial["revision"])
        self.assertEqual("conflict", error.exception.code)
        self.assertEqual(renamed, self.package.status())

    def test_archival_hides_browsing_but_retains_direct_access_and_references(self):
        self.package.release("1")
        self.manager.register_application(
            "workspace",
            prompts={
                "review": {
                    "kind": "managed",
                    "publisher_id": "local",
                    "package_id": "review",
                    "prompt": "review",
                }
            },
        )
        archived = self.package.archive(expected_revision=self.initial["revision"])
        self.assertTrue(archived["archived"])
        self.assertEqual([], self.manager.managed_catalog())
        self.assertEqual([], self.manager.managed_summaries())
        self.assertEqual([], self.manager.catalog()["managed"])
        self.assertEqual([archived], self.manager.managed_catalog(include_archived=True))
        self.assertEqual([archived], self.manager.managed_summaries(include_archived=True))
        self.assertEqual([archived], self.manager.catalog(include_archived=True)["managed"])
        self.assertIn("Original.", self.package.render("review")["output"])
        self.assertIn("Original.", self.manager.render_application("workspace", "review")["output"])
        self.assertIn("Original.", self.manager.render("review@1/review")["output"])
        self.assertTrue(self.manager.check_store()["ok"])
        self.assertEqual(
            archived["revision"],
            self.package.archive(expected_revision=archived["revision"])["revision"],
        )
        visible = self.package.unarchive(expected_revision=archived["revision"])
        self.assertFalse(visible.get("archived", False))
        self.assertEqual([visible], self.manager.managed_catalog())
        self.assertEqual(
            visible["revision"],
            self.package.unarchive(expected_revision=visible["revision"])["revision"],
        )

    def test_archived_customized_package_still_registers_and_reconciles_without_unarchiving(self):
        source = self.source()
        tool = self.manager.package("tool", "main")
        initial = tool.refresh(source, publisher_revision=1)
        renamed = tool.rename("Tool review", expected_revision=initial["revision"])
        custom = tool.save({"body.md": "User instructions."}, expected_revision=renamed["revision"])
        archived = tool.archive(expected_revision=custom["revision"])
        (source / "body.md").write_text("Publisher two.")
        pending = tool.register(source, publisher_revision=2)
        self.assertTrue(pending.status["archived"])
        self.assertTrue(pending.status["tracking"])
        self.assertTrue(pending.status["reconciliation_required"])
        self.assertEqual("Tool review", pending.status["label"])
        self.assertIn("User instructions.", pending.content)
        kept = tool.reconcile(expected_revision=pending.status["revision"], take="managed")
        self.assertTrue(kept["archived"])
        restored = tool.restore(initial["revision"], expected_revision=kept["revision"])
        self.assertTrue(restored["archived"])
        self.assertEqual("Tool review", restored["label"])
        self.assertEqual(initial["active_digest"], restored["active_digest"])
        self.assertNotEqual(archived["revision"], restored["revision"])

    def test_archival_does_not_freeze_an_unmodified_publisher_copy(self):
        source = self.source()
        tool = self.manager.package("tool", "main")
        first = tool.refresh(source, publisher_revision=1)
        tool.archive(expected_revision=first["revision"])
        (source / "body.md").write_text("Publisher two.")
        result = tool.register(source, publisher_revision=2)
        self.assertEqual("updated", result.status["outcome"])
        self.assertTrue(result.status["archived"])
        self.assertFalse(result.status["customized"])
        self.assertIn("Publisher two.", result.content)
        self.assertFalse(
            any(row.get("publisher_id") == "tool" for row in self.manager.managed_catalog())
        )

    def test_duplicate_is_create_only_with_its_own_history_and_release_namespace(self):
        copied = self.package.duplicate("review-copy", label="Personal copy")
        self.assertEqual("local", copied["publisher_id"])
        self.assertEqual("review-copy", copied["id"])
        self.assertFalse(copied["tracking"])
        self.assertFalse(copied["customized"])
        self.assertFalse(copied["reconciliation_required"])
        self.assertIsNone(copied["baseline"]["revision"])
        self.assertIsNone(copied["previous"])
        self.assertEqual(1, copied["sequence"])
        copy = self.manager.package("local", "review-copy")
        self.assertEqual(self.package.render("review")["output"], copy.render("review")["output"])
        self.assertEqual("review-copy", copy.resolve()["snapshot"]["pack"]["name"])
        self.assertEqual(self.initial, self.package.status())
        self.assertEqual(1, len(copy.history()))
        copy.save({"body.md": "Independent."}, expected_revision=copied["revision"])
        self.assertIn("Original.", self.package.render("review")["output"])
        self.assertIn("Independent.", copy.render("review")["output"])
        self.package.release("1")
        self.assertEqual("review-copy", copy.release("1")["name"])
        self.assertIn("Independent.", self.manager.render("review-copy@1/review")["output"])
        with self.assertRaises(PromptError) as error:
            self.package.duplicate("review-copy")
        self.assertEqual("conflict", error.exception.code)

    def test_duplicate_of_historical_or_archived_content_starts_visible_and_independent(self):
        saved = self.package.save(
            {"body.md": "Current."}, expected_revision=self.initial["revision"]
        )
        archived = self.package.archive(expected_revision=saved["revision"])
        copied = self.package.duplicate("historical-copy", revision=self.initial["revision"])
        current_copy = self.package.duplicate("current-copy")
        self.assertFalse(copied.get("archived", False))
        self.assertFalse(current_copy.get("archived", False))
        self.assertIn(
            "Original.", self.manager.package("local", "historical-copy").render("review")["output"]
        )
        self.assertIn(
            "Current.", self.manager.package("local", "current-copy").render("review")["output"]
        )
        self.assertEqual(archived, self.package.status())
        with self.assertRaises(PromptError):
            self.package.duplicate("../escape")
        with self.assertRaises(PromptError):
            self.package.duplicate("review")
        with self.assertRaises(PromptError):
            self.package.duplicate("invalid-copy", label=" ")
        self.assertFalse((self.manager.store.home / "managed/local/invalid-copy.json").exists())

    def test_copy_does_not_inherit_publisher_pending_updates_or_diagnostics(self):
        source = self.source()
        tool = self.manager.package("tool", "main")
        initial = tool.refresh(source, publisher_revision=1)
        tool.save({"body.md": "User version."}, expected_revision=initial["revision"])
        (source / "body.md").write_text("Publisher two.")
        tool.refresh(source, publisher_revision=2)
        self.manager.report_managed_sync_error("tool", "main", "Offline")
        copied = tool.duplicate("personal")
        self.assertIsNone(copied["sync_error"])
        self.assertEqual(copied["baseline"], copied["upstream"])
        self.assertFalse(copied["reconciliation_required"])
        self.assertIsNone(copied["baseline"]["revision"])
        copy = self.manager.package("local", "personal")
        shutil.rmtree(source)
        self.assertIn("User version.", copy.render("review")["output"])
        self.assertEqual("tracking_stopped", copy.refresh(source, publisher_revision=3)["outcome"])
        self.assertTrue(tool.status()["reconciliation_required"])

    def test_failed_metadata_or_duplicate_pointer_write_preserves_existing_content(self):
        before = self.files()
        write = Backend.write

        def fail_pointer(backend, path, row, *, immutable=False):
            if not immutable:
                raise OSError("Pointer write failed")
            return write(backend, path, row, immutable=immutable)

        with patch.object(Backend, "write", fail_pointer):
            with self.assertRaises(PromptError):
                self.package.archive(expected_revision=self.initial["revision"])
            with self.assertRaises(PromptError):
                self.package.duplicate("interrupted-copy")
        self.assertEqual(self.initial, self.package.status())
        self.assertFalse((self.manager.store.home / "managed/local/interrupted-copy.json").exists())
        for path, data in before.items():
            self.assertEqual(data, Path(path).read_bytes())
        self.assertTrue(self.manager.check_store()["ok"])
        self.package.duplicate("interrupted-copy")
        self.assertTrue(self.manager.check_store()["ok"])

    def test_concurrent_metadata_guards_and_duplicate_creation_have_one_winner(self):
        def organize(label):
            try:
                return self.package.rename(label, expected_revision=self.initial["revision"])
            except PromptError as error:
                return error.code

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(organize, ["First", "Second"]))
        self.assertEqual(1, sum(isinstance(row, dict) for row in results))
        self.assertIn("conflict", results)

        def copy(_):
            try:
                return self.package.duplicate("racing-copy")
            except PromptError as error:
                return error.code

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            copies = list(pool.map(copy, range(2)))
        self.assertEqual(1, sum(isinstance(row, dict) for row in copies))
        self.assertIn("conflict", copies)
        self.assertTrue(self.manager.check_store()["ok"])

    def test_archived_metadata_and_copies_survive_backup_restore(self):
        renamed = self.package.rename("Stored label", expected_revision=self.initial["revision"])
        archived = self.package.archive(expected_revision=renamed["revision"])
        copied = self.package.duplicate("backup-copy")
        archive = self.root / "library.zip"
        self.manager.backup_store(archive)
        restored = Manager(self.root / "restored", packs=[])
        restored.restore_store(archive)
        self.assertEqual(archived, restored.package("local", "review").status())
        self.assertEqual(copied, restored.package("local", "backup-copy").status())
        self.assertEqual([copied], restored.managed_catalog())
        self.assertEqual(2, len(restored.managed_catalog(include_archived=True)))
        self.assertTrue(restored.check_store()["ok"])

    def test_corrupt_archival_metadata_is_not_hidden_or_accepted_by_integrity_checks(self):
        row = self.manager._managed().load("local", "review")
        row["archived"] = "yes"
        row["revision"] = digest({key: value for key, value in row.items() if key != "revision"})
        atomic_json(self.manager.store.home / "managed/local/review.json", row)
        listed = self.manager.managed_catalog()
        self.assertEqual(1, len(listed))
        self.assertIn("error", listed[0])
        self.assertFalse(self.manager.check_store()["ok"])
        with self.assertRaises(PromptError):
            self.package.render("review")

    def test_cli_organization_and_archived_listing_match_sdk_results(self):
        def cli(*args):
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "prompt_manager",
                    "--home",
                    str(self.manager.store.home),
                    *args,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            return json.loads(result.stdout)

        named = cli(
            "managed",
            "rename",
            "local",
            "review",
            "--label",
            "CLI label",
            "--expected-revision",
            self.initial["revision"],
        )
        archived = cli(
            "managed", "archive", "local", "review", "--expected-revision", named["revision"]
        )
        self.assertEqual([], cli("managed", "list"))
        self.assertEqual([archived], cli("managed", "list", "--include-archived"))
        self.assertEqual([archived], cli("catalog", "--include-archived")["managed"])
        copy = cli("managed", "duplicate", "local", "review", "cli-copy", "--label", "CLI copy")
        self.assertEqual("cli-copy", copy["id"])
        visible = cli(
            "managed", "unarchive", "local", "review", "--expected-revision", archived["revision"]
        )
        cleared = cli(
            "managed",
            "rename",
            "local",
            "review",
            "--clear-label",
            "--expected-revision",
            visible["revision"],
        )
        self.assertNotIn("label", cleared)
