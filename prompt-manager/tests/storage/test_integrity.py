import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_manager import Manager, PromptError, StoreCheckResult, StoreFinding
from prompt_manager.codec import digest
from prompt_manager.managed.registration import Backend
from prompt_manager.storage.files import atomic_json
from prompt_manager.storage.objects import store_object


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "store"
        self.manager = Manager(self.home, packs=[])

    def seed(self):
        self.manager.create_managed("review", content="Original.")
        self.package = self.manager.package("local", "review")
        return self.package.status()

    def files(self):
        return {str(path): path.read_bytes() for path in self.home.rglob("*") if path.is_file()}

    def codes(self, result):
        self.assertTrue(StoreCheckResult.__required_keys__ <= result.keys())
        for row in result["findings"]:
            self.assertTrue(StoreFinding.__required_keys__ <= row.keys())
        return {row["code"] for row in result["findings"]}

    def test_absent_and_healthy_store_checks_are_read_only(self):
        absent = self.manager.check_store()
        self.assertTrue(absent["ok"])
        self.assertFalse(absent["exists"])
        self.assertFalse(self.home.exists())
        first = self.seed()
        self.package.save({"body.md": "Edited."}, expected_revision=first["revision"])
        self.package.release("1", revision=first["revision"])
        before = self.files()
        result = self.manager.check_store()
        self.assertTrue(result["ok"], result)
        self.assertEqual(set(), self.codes(result))
        self.assertEqual(2, result["counts"]["managed-history"])
        self.assertEqual(before, self.files())

    def test_missing_referenced_object_is_corruption_without_substitution(self):
        first = self.seed()
        (self.home / "objects" / (first["active_digest"][7:] + ".json")).unlink()
        result = self.manager.check_store()
        self.assertFalse(result["ok"])
        self.assertIn("object_missing", self.codes(result))
        with self.assertRaises(PromptError):
            self.package.render("review")

    def test_invalid_records_are_isolated_and_uncertain_orphans_are_not_claimed(self):
        first = self.seed()
        (self.home / "objects" / (first["active_digest"][7:] + ".json")).write_text("{}")
        (self.home / "sources").mkdir()
        (self.home / "sources/broken.json").write_text("{")
        result = self.manager.check_store()
        self.assertFalse(result["ok"])
        self.assertFalse(result["references_complete"])
        self.assertGreaterEqual(result["counts"]["errors"], 2)
        self.assertNotIn("unreferenced_object", self.codes(result))

    def test_missing_history_and_invalid_sequence_are_detected(self):
        first = self.seed()
        saved = self.package.save({"body.md": "Edited."}, expected_revision=first["revision"])
        history = self.home / "managed-history/local/review"
        (history / (first["revision"][7:] + ".json")).unlink()
        self.assertIn("history_link_missing", self.codes(self.manager.check_store()))
        (history / (saved["revision"][7:] + ".json")).unlink()
        self.assertIn("current_history_mismatch", self.codes(self.manager.check_store()))
        row = self.manager._managed().load("local", "review")
        row.update(previous=None, sequence=9)
        row["revision"] = digest({key: value for key, value in row.items() if key != "revision"})
        atomic_json(self.home / "managed/local/review.json", row)
        atomic_json(history / (row["revision"][7:] + ".json"), row)
        self.assertIn("history_sequence", self.codes(self.manager.check_store()))

    def test_unreferenced_artifacts_are_reported_and_pins_protect_history(self):
        first = self.seed()
        write = Backend.write

        def fail_pointer(backend, path, row, *, immutable=False):
            if not immutable:
                raise OSError("Pointer write failed")
            return write(backend, path, row, immutable=immutable)

        with patch.object(Backend, "write", fail_pointer):
            with self.assertRaises(PromptError):
                self.package.save({"body.md": "Uncommitted."}, expected_revision=first["revision"])
        artifact = self.home / "managed/local/.registry-interrupted"
        artifact.write_text("Incomplete temporary bytes")
        before = self.files()
        result = self.manager.check_store()
        self.assertTrue(result["ok"], result)
        self.assertTrue(
            {"unreferenced_object", "unreferenced_history", "temporary_file"} <= self.codes(result)
        )
        self.assertEqual(before, self.files())
        orphan = next(
            path
            for path in (self.home / "managed-history/local/review").glob("*.json")
            if path.stem != first["revision"][7:]
        )
        self.manager.register_application(
            "pinned",
            prompts={
                "review": {
                    "kind": "managed",
                    "publisher_id": "local",
                    "package_id": "review",
                    "prompt": "review",
                    "revision": "sha256:" + orphan.stem,
                }
            },
        )
        protected = self.manager.check_store()
        self.assertNotIn("unreferenced_history", self.codes(protected))
        self.assertNotIn("unreferenced_object", self.codes(protected))

    def test_digest_only_mapping_is_an_object_root(self):
        self.seed()
        snapshot = self.package.resolve()["snapshot"]
        snapshot["files"]["body.md"] = "Only a digest mapping retains this."
        identity = store_object(self.home, snapshot)
        self.assertIn("unreferenced_object", self.codes(self.manager.check_store()))
        self.manager.register_application(
            "pinned",
            prompts={
                "review": {
                    "kind": "release",
                    "reference": "review@" + identity + "/review",
                }
            },
        )
        self.assertNotIn("unreferenced_object", self.codes(self.manager.check_store()))

    def test_external_availability_is_separate_from_owned_corruption(self):
        source = self.root / "missing-draft"
        self.manager.register_source("external", [source], expected_revision=None)
        self.manager.register_application(
            "external",
            prompts={
                "review": {
                    "kind": "draft",
                    "source_id": "external",
                    "pack_path": str(source),
                    "prompt": "review",
                }
            },
        )
        before = self.files()
        result = self.manager.check_store()
        self.assertTrue(result["ok"], result)
        self.assertEqual({"source_unavailable", "mapping_unresolved"}, self.codes(result))
        self.assertTrue(all(row["category"] == "availability" for row in result["findings"]))
        self.assertEqual([], self.manager.check_store(include_external=False)["findings"])
        self.assertEqual(before, self.files())

    def test_changes_during_inspection_are_reported_without_orphan_claims(self):
        self.seed()
        snapshot = Backend.snapshot
        changed = False

        def read_then_write(backend, identity):
            nonlocal changed
            captured = snapshot(backend, identity)
            if not changed:
                changed = True
                self.manager.create_managed("concurrent", content="Another writer.")
            return captured

        with patch.object(Backend, "snapshot", read_then_write):
            result = self.manager.check_store()
        self.assertFalse(result["stable"])
        self.assertFalse(result["ok"])
        self.assertIn("store_changed", self.codes(result))
        self.assertNotIn("unreferenced_object", self.codes(result))

    def test_access_errors_report_incomplete_inspection(self):
        self.seed()
        with patch.object(Backend, "snapshot", side_effect=PermissionError("Denied")):
            result = self.manager.check_store()
        self.assertFalse(result["complete"])
        self.assertFalse(result["ok"])
        self.assertTrue(any(row["category"] == "access" for row in result["findings"]))

    def test_symlinks_are_not_followed(self):
        first = self.seed()
        external = self.root / "outside.json"
        path = self.home / "objects" / (first["active_digest"][7:] + ".json")
        path.rename(external)
        try:
            os.symlink(external, path)
        except OSError as error:
            self.skipTest(str(error))
        before = external.read_bytes()
        result = self.manager.check_store()
        self.assertFalse(result["complete"])
        self.assertEqual(before, external.read_bytes())

    def test_non_directory_store_is_reported_as_inaccessible(self):
        self.home.write_text("A file cannot hold store records")
        before = self.home.read_bytes()
        result = self.manager.check_store()
        self.assertTrue(result["exists"])
        self.assertFalse(result["complete"])
        self.assertFalse(result["ok"])
        self.assertEqual(before, self.home.read_bytes())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO creation is not available")
    def test_special_files_are_reported_without_blocking_reads(self):
        self.seed()
        os.mkfifo(self.home / "objects" / (("a" * 64) + ".json"))
        checked = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json,sys; from prompt_manager import Manager; "
                "print(json.dumps(Manager(sys.argv[1], packs=[]).check_store()))",
                str(self.home),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(0, checked.returncode, checked.stderr)
        self.assertFalse(json.loads(checked.stdout)["complete"])

    def test_cli_outputs_structured_results_and_exit_status(self):
        def cli():
            return subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "prompt_manager",
                    "--home",
                    str(self.home),
                    "store",
                    "check",
                    "--no-external",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

        absent = cli()
        self.assertEqual(0, absent.returncode, absent.stderr)
        self.assertFalse(self.home.exists())
        self.seed()
        (self.home / "managed/local/review.json").write_text("{")
        failed = cli()
        self.assertEqual(1, failed.returncode)
        self.assertFalse(json.loads(failed.stdout)["ok"])
        self.assertEqual("", failed.stderr)
