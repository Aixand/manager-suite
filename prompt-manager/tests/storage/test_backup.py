import hashlib
import json
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch

from prompt_manager import Manager, PromptError, StoreBackupResult, StoreRestoreResult
from prompt_manager.codec import MAX_BYTES
from prompt_manager.library import backup
from prompt_manager.storage.objects import store_object


class BackupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.manager = Manager(self.root / "source-store", packs=[])
        self.archive = self.root / "backup.zip"
        self.target = self.root / "restored"

    def seed(self):
        initial = self.manager.create_managed("review", content="Original.")
        self.package = self.manager.package("local", "review")
        edited = self.package.save(
            {"body.md": "My version."}, expected_revision=initial["revision"]
        )
        self.package.release("1", revision=initial["revision"])
        external = self.root / "external"
        external.mkdir()
        (external / "private-project.txt").write_text("Do not copy project data")
        self.manager.register_source("external", [external], expected_revision=None)
        self.manager.register_application(
            "workspace",
            prompts={
                "review": {
                    "kind": "managed",
                    "publisher_id": "local",
                    "package_id": "review",
                    "prompt": "review",
                    "revision": initial["revision"],
                }
            },
        )
        return initial, edited

    def records(self, home):
        return {
            path.relative_to(home).as_posix(): path.read_bytes()
            for section in backup.SECTIONS
            for path in (home / section).rglob("*.json")
        }

    def contents(self):
        with zipfile.ZipFile(self.archive) as archive:
            values = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(values.pop("manifest.json"))
        return manifest, values

    def altered(self, name, change):
        manifest, values = self.contents()
        change(manifest, values)
        path = self.root / name
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            for relative, data in values.items():
                archive.writestr(relative, data)
        return path

    def assert_rejected(self, path):
        before = self.records(self.manager.store.home)
        with self.assertRaises(PromptError):
            Manager(self.target, packs=[]).restore_store(path)
        self.assertFalse(self.target.exists())
        self.assertEqual([], list(self.root.glob(".prompt-restore-*")))
        self.assertEqual(before, self.records(self.manager.store.home))

    def test_backup_warning_paths_survive_a_symlinked_temporary_directory(self):
        self.seed()
        snapshot = self.package.resolve()["snapshot"]
        snapshot["files"]["body.md"] = "Unreferenced content."
        store_object(self.manager.store.home, snapshot)
        directory = self.root / "temporary"
        directory.mkdir()
        alias = self.root / "temporary-alias"
        try:
            alias.symlink_to(directory, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlinks are unavailable")
        with patch.object(tempfile, "tempdir", str(alias)):
            result = self.manager.backup_store(self.archive)
        self.assertTrue(result["validated"])
        self.assertTrue(result["warnings"])
        self.assertTrue(all(not Path(row["path"]).is_absolute() for row in result["warnings"]))

    def test_roundtrip_preserves_record_bytes_history_pins_releases_and_metadata(self):
        initial, edited = self.seed()
        snapshot = self.package.resolve()["snapshot"]
        snapshot["files"]["body.md"] = "Unreferenced content retained too."
        orphan = store_object(self.manager.store.home, snapshot)
        artifact = self.manager.store.home / "managed/local/.registry-interrupted"
        artifact.write_text("An incomplete temporary buffer")
        before = self.records(self.manager.store.home)
        backed = self.manager.backup_store(self.archive)
        self.assertTrue(StoreBackupResult.__required_keys__ <= backed.keys())
        self.assertTrue(backed["validated"])
        self.assertFalse(backed["external_drafts_included"])
        self.assertIn("locks/", backed["excluded"])
        self.assertIn("managed/local/.registry-interrupted", backed["excluded"])
        self.assertTrue(any(row["code"] == "unreferenced_object" for row in backed["warnings"]))
        self.assertEqual(before, self.records(self.manager.store.home))
        manifest, values = self.contents()
        self.assertEqual(before, values)
        self.assertEqual(set(before), set(manifest["files"]))
        self.assertNotIn("private-project.txt", " ".join(values))
        self.assertEqual(
            [{"id": "external", "paths": [str(self.root / "external")]}], backed["external_sources"]
        )
        shutil.rmtree(self.root / "external")
        restored = Manager(self.target, packs=[])
        result = restored.restore_store(self.archive)
        self.assertTrue(StoreRestoreResult.__required_keys__ <= result.keys())
        self.assertEqual(backed["digest"], result["digest"])
        self.assertEqual(before, self.records(self.target))
        self.assertFalse((self.target / "locks").exists())
        self.assertIn("My version.", restored.package("local", "review").render("review")["output"])
        self.assertIn("Original.", restored.render_application("workspace", "review")["output"])
        self.assertIn("Original.", restored.render("review@1/review")["output"])
        self.assertEqual(
            edited["revision"], restored.package("local", "review").status()["revision"]
        )
        self.assertEqual(
            initial["revision"], restored.package("local", "review").history()[-1]["revision"]
        )
        self.assertTrue((self.target / "objects" / (orphan[7:] + ".json")).exists())
        checked = restored.check_store()
        self.assertTrue(checked["ok"])
        self.assertTrue(any(row["code"] == "source_unavailable" for row in checked["findings"]))

    def test_absent_store_backup_and_empty_restore_need_no_source_creation(self):
        result = self.manager.backup_store(self.archive)
        self.assertEqual(0, result["file_count"])
        self.assertFalse(self.manager.store.home.exists())
        restored = Manager(self.target, packs=[])
        restored.restore_store(self.archive)
        self.assertTrue(restored.check_store()["ok"])
        self.assertEqual([], list(self.target.iterdir()))

    def test_archive_is_immutable_and_identical_retry_does_not_replace_it(self):
        self.seed()
        first = self.manager.backup_store(self.archive)
        original = self.archive.read_bytes()
        identity = self.archive.stat().st_ino
        self.assertEqual(first, self.manager.backup_store(self.archive))
        self.assertEqual(identity, self.archive.stat().st_ino)
        self.package.save(
            {"body.md": "Changed."}, expected_revision=self.package.status()["revision"]
        )
        with self.assertRaises(PromptError) as error:
            self.manager.backup_store(self.archive)
        self.assertEqual("conflict", error.exception.code)
        self.assertEqual(original, self.archive.read_bytes())

    def test_existing_store_or_empty_directory_is_never_a_restore_target(self):
        self.seed()
        self.manager.backup_store(self.archive)
        before = self.records(self.manager.store.home)
        with self.assertRaises(PromptError):
            self.manager.restore_store(self.archive)
        self.assertEqual(before, self.records(self.manager.store.home))
        self.target.mkdir()
        with self.assertRaises(PromptError):
            Manager(self.target, packs=[]).restore_store(self.archive)
        self.assertEqual([], list(self.target.iterdir()))

    def test_corrupt_source_is_rejected_without_publishing_an_archive(self):
        self.seed()
        current = self.manager.store.home / "managed/local/review.json"
        current.write_text("{")
        with self.assertRaises(PromptError):
            self.manager.backup_store(self.archive)
        self.assertFalse(self.archive.exists())
        with self.assertRaises(PromptError):
            self.manager.backup_store(self.manager.store.home / "recursive.zip")

    def test_written_archive_is_verified_before_publication(self):
        self.seed()
        write = backup._zip_write

        def corrupt_generated_record(archive, name, data):
            write(archive, name, b"{}" if name.startswith("objects/") else data)

        with patch.object(backup, "_zip_write", side_effect=corrupt_generated_record):
            with self.assertRaises(PromptError) as error:
                self.manager.backup_store(self.archive)
        self.assertEqual("integrity", error.exception.code)
        self.assertFalse(self.archive.exists())
        self.assertEqual([], list(self.root.glob(".backup-*")))

    def test_changed_source_aborts_capture(self):
        self.seed()
        write = backup._write
        changed = False

        def write_then_edit(*args, **options):
            nonlocal changed
            write(*args, **options)
            if not changed:
                changed = True
                self.package.save(
                    {"body.md": "Concurrent edit."},
                    expected_revision=self.package.status()["revision"],
                )

        with patch.object(backup, "_write", side_effect=write_then_edit):
            with self.assertRaises(PromptError) as error:
                self.manager.backup_store(self.archive)
        self.assertEqual("conflict", error.exception.code)
        self.assertFalse(self.archive.exists())

    def test_checksums_and_complete_reference_graph_are_both_verified(self):
        self.seed()
        self.manager.backup_store(self.archive)

        def checksum_mismatch(manifest, values):
            path = next(path for path in values if path.startswith("objects/"))
            values[path] = b"{}"

        def valid_checksum_but_invalid_state(manifest, values):
            path = "managed/local/review.json"
            row = json.loads(values[path])
            row["active_digest"] = "sha256:" + "0" * 64
            data = json.dumps(row).encode()
            values[path] = data
            manifest["files"][path] = {
                "size": len(data),
                "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
            }

        def missing_object(manifest, values):
            path = next(path for path in values if path.startswith("objects/"))
            del values[path]
            del manifest["files"][path]

        for index, alter in enumerate(
            (checksum_mismatch, valid_checksum_but_invalid_state, missing_object)
        ):
            with self.subTest(case=index):
                self.assert_rejected(self.altered(f"corrupt-{index}.zip", alter))
        truncated = self.root / "truncated.zip"
        truncated.write_bytes(self.archive.read_bytes()[:100])
        self.assert_rejected(truncated)

    def test_unknown_schema_traversal_and_unlisted_entries_are_rejected(self):
        self.seed()
        self.manager.backup_store(self.archive)

        def schema(manifest, values):
            manifest["schema"] = "unknown"

        def traversal(manifest, values):
            values["../escaped.json"] = b"{}"
            manifest["files"]["../escaped.json"] = {
                "size": 2,
                "digest": "sha256:" + hashlib.sha256(b"{}").hexdigest(),
            }

        def unlisted(manifest, values):
            values["applications/unlisted.json"] = b"{}"

        for index, alter in enumerate((schema, traversal, unlisted)):
            with self.subTest(case=index):
                self.assert_rejected(self.altered(f"invalid-{index}.zip", alter))
        self.assertFalse((self.root / "escaped.json").exists())

    def test_link_and_duplicate_entries_are_rejected(self):
        self.seed()
        self.manager.backup_store(self.archive)
        duplicate = self.root / "duplicate.zip"
        duplicate.write_bytes(self.archive.read_bytes())
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(duplicate, "a") as archive:
                archive.writestr("manifest.json", "{}")
        self.assert_rejected(duplicate)
        manifest, values = self.contents()
        linked = self.root / "link.zip"
        with zipfile.ZipFile(linked, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            for index, (name, data) in enumerate(values.items()):
                info = zipfile.ZipInfo(name)
                info.external_attr = ((stat.S_IFLNK if index == 0 else stat.S_IFREG) | 0o600) << 16
                archive.writestr(info, data)
        self.assert_rejected(linked)

    def test_limits_and_misleading_external_metadata_are_rejected(self):
        self.seed()
        with patch.object(backup, "MAX_TOTAL_BYTES", 1):
            with self.assertRaises(PromptError) as error:
                self.manager.backup_store(self.archive)
        self.assertEqual("size", error.exception.code)
        self.assertFalse(self.archive.exists())
        self.manager.backup_store(self.archive)

        def bad_sources(manifest, values):
            manifest["external_sources"] = []

        self.assert_rejected(self.altered("bad-sources.zip", bad_sources))

        def excessive_size(manifest, values):
            next(iter(manifest["files"].values()))["size"] = MAX_BYTES + 1

        self.assert_rejected(self.altered("bad-size.zip", excessive_size))

    def test_failed_archive_publication_and_restore_staging_leave_no_visible_result(self):
        self.seed()
        with patch.object(backup.os, "link", side_effect=OSError("Archive publication failed")):
            with self.assertRaises(PromptError):
                self.manager.backup_store(self.archive)
        self.assertFalse(self.archive.exists())
        self.assertEqual([], list(self.root.glob(".backup-*")))
        self.manager.backup_store(self.archive)
        write = backup._write

        def fail_after_staged_file(*args, **options):
            write(*args, **options)
            raise OSError("Staging interrupted")

        with patch.object(backup, "_write", side_effect=fail_after_staged_file):
            self.assert_rejected(self.archive)

    def test_destination_created_during_validation_is_preserved(self):
        self.seed()
        self.manager.backup_store(self.archive)
        validate = backup._validate

        def validate_then_create(root):
            result = validate(root)
            self.target.mkdir()
            (self.target / "other-writer.txt").write_text("Keep this")
            return result

        with patch.object(backup, "_validate", side_effect=validate_then_create):
            with self.assertRaises(PromptError) as error:
                Manager(self.target, packs=[]).restore_store(self.archive)
        self.assertEqual("conflict", error.exception.code)
        self.assertEqual("Keep this", (self.target / "other-writer.txt").read_text())

    def test_pending_updates_tracking_and_diagnostics_survive_restore(self):
        self.seed()
        bundled = self.root / "bundled"
        bundled.mkdir()
        snapshot = self.package.resolve()["snapshot"]
        (bundled / "pack.json").write_text(json.dumps(snapshot["pack"]))
        (bundled / "body.md").write_text("Publisher one.")
        prompts = self.manager.package("tool", "main")
        first = prompts.refresh(bundled, publisher_revision=1)
        prompts.save({"body.md": "User instructions."}, expected_revision=first["revision"])
        (bundled / "body.md").write_text("Publisher two.")
        pending = prompts.refresh(bundled, publisher_revision=2)
        prompts.set_tracking(False, expected_revision=pending["revision"])
        self.manager.report_managed_sync_error("tool", "main", "Retained diagnostic")
        original = prompts.status()
        self.manager.backup_store(self.archive)
        restored = Manager(self.target, packs=[])
        restored.restore_store(self.archive)
        current = restored.package("tool", "main")
        self.assertEqual(original, current.status())
        self.assertTrue(current.status()["reconciliation_required"])
        self.assertFalse(current.status()["tracking"])
        self.assertIn("User instructions.", current.render("review")["output"])
        preview = current.preview_reconciliation()
        kept = current.reconcile(expected_revision=preview["revision"], take="managed")
        self.assertFalse(kept["reconciliation_required"])
        self.assertIn("User instructions.", current.render("review")["output"])
        with self.assertRaises(PromptError):
            restored.open_package(prompts.reference)

    def test_process_exit_before_and_after_restore_publication(self):
        self.seed()
        self.manager.backup_store(self.archive)
        worker = r"""
import os,sys
from unittest.mock import patch
from prompt_manager import Manager
from prompt_manager.library import backup
rename = os.rename
phase = sys.argv[3]
def interrupted(source, target):
    if phase == "after":
        rename(source, target)
    os._exit(72)
with patch.object(backup.os, "rename", interrupted):
    Manager(sys.argv[1], packs=[]).restore_store(sys.argv[2])
"""
        before = self.records(self.manager.store.home)
        for phase in ("before", "after"):
            target = self.root / ("restore-" + phase)
            result = subprocess.run(
                [sys.executable, "-c", worker, str(target), str(self.archive), phase],
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(72, result.returncode, result.stderr)
            self.assertEqual(phase == "after", target.exists())
            if target.exists():
                self.assertEqual(before, self.records(target))
                self.assertTrue(Manager(target, packs=[]).check_store()["ok"])
        self.assertEqual(before, self.records(self.manager.store.home))

    def test_archive_changed_during_restore_never_activates_target(self):
        self.seed()
        self.manager.backup_store(self.archive)
        original = backup._file_digest

        def digest_then_change(path):
            result = original(path)
            with path.open("ab") as stream:
                stream.write(b"Changed after validation")
            return result

        with patch.object(backup, "_file_digest", side_effect=digest_then_change):
            with self.assertRaises(PromptError) as error:
                Manager(self.target, packs=[]).restore_store(self.archive)
        self.assertEqual("conflict", error.exception.code)
        self.assertFalse(self.target.exists())

    def test_failure_after_restore_promotion_leaves_a_complete_reopenable_store(self):
        self.seed()
        self.manager.backup_store(self.archive)
        synchronize = backup.sync_directory

        def fail_after_promotion(directory):
            if directory == self.target.parent and self.target.exists():
                raise OSError("Directory synchronization failed after promotion")
            synchronize(directory)

        restored = Manager(self.target, packs=[])
        with patch.object(backup, "sync_directory", side_effect=fail_after_promotion):
            with self.assertRaises(PromptError) as error:
                restored.restore_store(self.archive)
        self.assertEqual("io", error.exception.code)
        self.assertTrue(restored.check_store()["ok"])
        self.assertEqual(self.records(self.manager.store.home), self.records(self.target))
        with self.assertRaises(PromptError):
            restored.restore_store(self.archive)

    def test_cli_backup_and_restore_use_selected_home_and_json_results(self):
        self.seed()

        def cli(home, *args):
            result = subprocess.run(
                [sys.executable, "-m", "prompt_manager", "--home", str(home), "store", *args],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            return json.loads(result.stdout)

        created = cli(self.manager.store.home, "backup", str(self.archive))
        restored = cli(self.target, "restore", str(self.archive))
        self.assertEqual(created["digest"], restored["digest"])
        self.assertEqual(str(self.target), restored["store"])
        self.assertTrue(Manager(self.target, packs=[]).check_store()["ok"])
