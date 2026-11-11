"""One-call registration returns effective content and its captured status."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_manager import Manager, PromptError, RegistrationResult
from prompt_manager.prompts.rendering import render as render_snapshot


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manager = Manager(self.root / "store", packs=[])
        self.source = self.root / "source"
        self.source.mkdir()
        self.manifest = {
            "schema": "prompt-manager/pack-1",
            "name": "review",
            "sections": {"body": {"file": "body.md"}},
            "prompts": {"review": {"sections": [{"id": "body", "section": "body"}]}},
        }
        self.write_manifest()
        self.write("Publisher one.")
        self.package = self.manager.package("tool")

    def write_manifest(self):
        (self.source / "pack.json").write_text(json.dumps(self.manifest))

    def write(self, text):
        (self.source / "body.md").write_text(text)

    def register(self, revision=1, **options):
        return self.package.register(self.source, publisher_revision=revision, **options)

    def test_first_repeat_update_and_result_contract(self):
        first = self.register()
        self.assertIsInstance(first, RegistrationResult)
        self.assertEqual("imported", first.status["outcome"])
        self.assertEqual("[user]\nPublisher one.\n", first.content)
        repeated = self.register()
        self.assertEqual("unchanged", repeated.status["outcome"])
        self.assertEqual(first.status["revision"], repeated.status["revision"])
        self.write("Publisher two.")
        updated = self.register(2)
        self.assertEqual("updated", updated.status["outcome"])
        self.assertIn("Publisher two.", updated.content)
        serialized = json.loads(json.dumps(updated.to_dict()))
        self.assertEqual("prompt-manager/registration-result-1", serialized["schema"])
        self.assertEqual(updated.content, serialized["content"])
        serialized["status"]["baseline"]["digest"] = "caller mutation"
        self.assertNotEqual(serialized["status"]["baseline"], updated.status["baseline"])

    def test_customized_content_wins_until_user_applies_reconciliation(self):
        first = self.register()
        self.package.save(
            {"body.md": "My instructions."}, expected_revision=first.status["revision"]
        )
        self.assertIn("My instructions.", self.register().content)
        self.write("New publisher instructions.")
        pending = self.register(2)
        self.assertEqual("update_pending", pending.status["outcome"])
        self.assertTrue(pending.status["reconciliation_required"])
        self.assertIn("My instructions.", pending.content)
        accepted = self.package.reconcile(
            expected_revision=pending.status["revision"], take="publisher"
        )
        current = self.register(2)
        self.assertFalse(current.status["reconciliation_required"])
        self.assertEqual(accepted["revision"], current.status["revision"])
        self.assertIn("New publisher instructions.", current.content)

    def test_failed_ingestion_returns_verified_content_and_can_recover(self):
        first = self.register()
        self.write("{{ undeclared }}")
        fallback = self.register(2)
        self.assertEqual(first.content, fallback.content)
        self.assertEqual("refresh_failed", fallback.status["outcome"])
        self.assertIsNotNone(fallback.status["sync_error"])
        self.assertEqual(first.status["active_digest"], fallback.status["active_digest"])
        self.assertEqual(fallback.status["revision"], self.package.status()["revision"])
        self.write("Valid update.")
        recovered = self.register(2)
        self.assertIsNone(recovered.status["sync_error"])
        self.assertEqual("updated", recovered.status["outcome"])
        shutil.rmtree(self.source)
        missing = self.register(3)
        self.assertEqual(recovered.content, missing.content)
        self.assertEqual("io", missing.status["sync_error"]["code"])
        # The lower-level refresh remains strict for explicit callers.
        with self.assertRaises(PromptError):
            self.package.refresh(self.source, publisher_revision=3)

    def test_initial_failure_and_corrupt_accepted_content_raise(self):
        self.write("{{ undeclared }}")
        with self.assertRaises(PromptError):
            self.register()
        self.assertFalse((self.manager.store.home / "managed/tool/default.json").exists())
        self.write("Publisher one.")
        first = self.register()
        identity = first.status["active_digest"][7:]
        (self.manager.store.home / "objects" / (identity + ".json")).write_text("{}")
        shutil.rmtree(self.source)
        with self.assertRaises(PromptError) as error:
            self.register(2)
        self.assertEqual("integrity", error.exception.code)

    def test_older_conflicting_and_stopped_registrations(self):
        first = self.register(2)
        self.write("Different.")
        older = self.register(1)
        self.assertEqual(first.content, older.content)
        self.assertEqual("ignored_older", older.status["outcome"])
        conflict = self.register(2)
        self.assertEqual(first.content, conflict.content)
        self.assertEqual("refresh_failed", conflict.status["outcome"])
        self.assertEqual("conflict", conflict.status["sync_error"]["code"])
        stopped = self.package.set_tracking(False, expected_revision=conflict.status["revision"])
        shutil.rmtree(self.source)
        retained = self.register(3)
        self.assertEqual("tracking_stopped", retained.status["outcome"])
        self.assertEqual(stopped["revision"], retained.status["revision"])
        self.assertEqual(first.content, retained.content)
        with self.assertRaises(PromptError):
            self.register(-1)

    def test_prompt_selection_inputs_and_messages_are_explicit(self):
        self.manifest["sections"]["body"]["inputs"] = {"task": {"type": "string"}}
        recipe = self.manifest["prompts"]["review"]
        recipe["inputs"] = {"task": {"type": "string"}}
        recipe["sections"][0]["inputs"] = {"task": {"input": "task"}}
        self.manifest["prompts"]["other"] = recipe
        self.write_manifest()
        self.write("Review {{ task }}.")
        with self.assertRaises(PromptError):
            self.register(inputs={"task": "parser"})
        result = self.register(prompt_id="review", inputs={"task": "parser"}, format="messages")
        self.assertEqual([{"role": "user", "content": "Review parser."}], result.content)
        with self.assertRaises(PromptError):
            self.register(prompt_id="review")
        self.assertIsNone(self.package.status()["sync_error"])

    def test_content_and_status_stay_on_the_captured_revision_during_concurrent_edit(self):
        first = self.register()

        def edit_after_capture(*args, **kwargs):
            self.package.save(
                {"body.md": "Later user edit."}, expected_revision=first.status["revision"]
            )
            return render_snapshot(*args, **kwargs)

        with patch("prompt_manager.manager.render_snapshot", side_effect=edit_after_capture):
            captured = self.register()
        self.assertEqual(first.content, captured.content)
        self.assertEqual(first.status["revision"], captured.status["revision"])
        self.assertIn("Later user edit.", self.package.render("review")["output"])

    def test_unordered_changes_require_review_without_replacing_content(self):
        first = self.package.register(self.source)
        self.write("Unordered update.")
        pending = self.package.register(self.source)
        self.assertEqual(first.content, pending.content)
        self.assertEqual("update_pending", pending.status["outcome"])
        self.assertTrue(pending.status["reconciliation_required"])

    def test_failed_status_write_can_return_verified_content_with_ephemeral_diagnostic(self):
        first = self.register()
        self.write("Publisher two.")
        with patch(
            "prompt_manager.managed.state.Managed.commit", side_effect=PermissionError("Read only")
        ):
            result = self.register(2)
        self.assertEqual(first.content, result.content)
        self.assertEqual(first.status["revision"], result.status["revision"])
        self.assertEqual("refresh_failed", result.status["outcome"])
        self.assertEqual("io", result.status["sync_error"]["code"])
        self.assertIsNone(self.package.status()["sync_error"])

    def test_direct_manager_api_and_cli_return_the_same_pair(self):
        direct = self.manager.register_prompt("tool", self.source, publisher_revision=1)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "prompt_manager",
                "--home",
                str(self.manager.store.home),
                "managed",
                "register",
                "tool",
                "default",
                str(self.source),
                "--publisher-revision",
                "1",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        cli = json.loads(result.stdout)
        self.assertEqual(direct.content, cli["content"])
        self.assertEqual(direct.status["revision"], cli["status"]["revision"])
        self.assertEqual("unchanged", cli["status"]["outcome"])
