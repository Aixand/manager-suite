"""Standalone local creation and owned import contracts."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from prompt_manager import Manager, PromptError


class LocalAuthoringTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name) / "store"
        self.manager = Manager(self.home, packs=[])

    def test_personal_prompt_creation_and_repeated_import_preserve_edits(self):
        created = self.manager.create_managed("review", content="Review carefully.")
        snapshot = self.manager.resolve_managed("local", created["id"])["snapshot"]
        source = self.home.parent / "draft"
        source.mkdir()
        (source / "pack.json").write_text(json.dumps(snapshot["pack"]))
        for file, text in snapshot["files"].items():
            (source / file).write_text(text)
        imported = self.manager.import_local(source)
        edited = self.manager.save_managed(
            "local",
            imported["id"],
            {"body.md": "My instructions."},
            expected_revision=imported["revision"],
        )
        repeated = self.manager.import_local(source)
        self.assertEqual(edited["revision"], repeated["revision"])
        (source / "body.md").write_text("New publisher instructions.")
        pending = self.manager.import_local(source)
        self.assertTrue(pending["reconciliation_required"])
        shutil.rmtree(source)
        self.assertIn(
            "My instructions.",
            self.manager.render_managed("local", imported["id"], "review")["output"],
        )
        kept = self.manager.reconcile(
            "local", imported["id"], expected_revision=pending["revision"], take="managed"
        )
        self.assertFalse(kept["reconciliation_required"])

    def test_creation_is_create_only_and_invalid_content_is_not_activated(self):
        first = self.manager.create_managed("review", content="My instructions.")
        with self.assertRaises(PromptError):
            self.manager.create_managed("review", content="Replacement.")
        self.assertEqual(
            first["revision"], self.manager.managed_status("local", "review")["revision"]
        )
        with self.assertRaises(PromptError):
            self.manager.create_managed("invalid", content="{{ missing }}")
        self.assertFalse((self.home / "managed/local/invalid.json").exists())

    def test_snapshot_import_captures_content_with_stable_explicit_identity(self):
        self.manager.create_managed("review", content="Keep this.", role="system")
        snapshot = self.manager.resolve_managed("local", "review")["snapshot"]
        source = self.home.parent / "snapshot.json"
        source.write_text(json.dumps(snapshot))
        imported = self.manager.import_local(source, package_id="portable")
        moved = source.with_name("moved.json")
        source.rename(moved)
        self.assertEqual(
            imported["revision"],
            self.manager.import_local(moved, package_id="portable")["revision"],
        )
        moved.unlink()
        result = self.manager.render_managed("local", "portable", "review", format="messages")
        self.assertEqual([{"role": "system", "content": "Keep this."}], result["output"])

    def test_cli_creation_runs_directly_without_jobs(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "prompt_manager",
                "--home",
                str(self.home),
                "managed",
                "create",
                "review",
                "--content",
                "Check the supplied work.",
            ],
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("review", json.loads(result.stdout)["id"])
        self.assertIn(
            "Check the supplied work.",
            self.manager.render_managed("local", "review", "review")["output"],
        )
