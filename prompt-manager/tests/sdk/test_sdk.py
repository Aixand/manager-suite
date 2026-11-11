"""Versioned public imports, explicit handles, and complete owned workflows."""

import inspect
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_manager import (
    SDK_CONTRACT_VERSION,
    ComparisonResult,
    ExportResult,
    ManagedResolution,
    ManagedState,
    ManagedStatus,
    Manager,
    Package,
    PackageReference,
    PromptError,
    ReconciliationPreview,
    ReleaseIdentity,
    RenderResult,
    ValidationResult,
)


class SDKTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manager = Manager(self.root / "store", packs=[])
        self.package = self.manager.package("publisher", "main")

    def seed(self):
        self.manager.create_managed("review", content="Original.\n")
        snapshot = self.manager.package("local", "review").resolve()["snapshot"]
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "pack.json").write_text(json.dumps(snapshot["pack"]))
        (self.source / "body.md").write_text(snapshot["files"]["body.md"])
        return self.package.refresh(self.source, publisher_revision=1)

    def assert_contract(self, contract, result):
        self.assertTrue(contract.__required_keys__ <= result.keys(), (contract, result))
        json.dumps(result, allow_nan=False)

    def test_public_contract_and_signatures(self):
        self.assertEqual(1, SDK_CONTRACT_VERSION)
        self.assertIsInstance(self.package, Package)
        for method in (Package.save, Package.reconcile, Package.restore, Package.set_tracking):
            parameter = inspect.signature(method).parameters["expected_revision"]
            self.assertEqual(inspect.Parameter.KEYWORD_ONLY, parameter.kind)
            self.assertIs(inspect.Parameter.empty, parameter.default)
        self.assertIn("prompt_id", inspect.signature(Package.render).parameters)
        self.assertIn("publisher_id", inspect.signature(Manager.package).parameters)
        self.assertIn("package_id", inspect.signature(Manager.package).parameters)

    def test_reference_roundtrip_and_recovery_without_side_effects(self):
        value = json.loads(json.dumps(self.package.reference.to_dict()))
        restored = PackageReference.from_dict(value)
        self.assertEqual(self.package.reference, restored)
        self.assertEqual(self.package, self.manager.open_package(value))
        self.assertFalse(self.manager.store.home.exists())
        with self.assertRaises(PromptError) as error:
            Manager(self.root / "other").open_package(value)
        self.assertEqual("conflict", error.exception.code)
        self.assertFalse((self.root / "other").exists())
        for invalid in (
            {**value, "schema": "unknown"},
            {**value, "extra": True},
            {**value, "publisher_id": "../escape"},
            {**value, "store": "relative"},
        ):
            with self.assertRaises(PromptError):
                self.manager.open_package(invalid)
        with self.assertRaises(PromptError):
            self.package.status()
        self.assertFalse(self.manager.store.home.exists())

    def test_explicit_refresh_failure_preserves_accepted_content(self):
        self.seed()
        shutil.rmtree(self.source)
        # Reopening is read-only and doesn't try a saved publisher path.
        reopened = self.manager.open_package(self.package.reference)
        self.assertIn("Original.", reopened.render("review")["output"])
        with self.assertRaises(PromptError):
            reopened.refresh(self.source, publisher_revision=2)
        self.assertIsNotNone(reopened.status()["sync_error"])
        self.assertIn("Original.", reopened.render("review")["output"])

    def test_user_precedence_reconciliation_history_pins_and_release(self):
        initial = self.seed()
        self.assert_contract(ManagedStatus, initial)
        inspected = self.package.inspect(
            path="/files/body.md", limit=8, expected_revision=initial["revision"]
        )
        self.assertEqual("Original", inspected["sides"]["managed"]["text"])

        edited = self.package.save(
            {"body.md": "My version.\n"}, expected_revision=initial["revision"]
        )
        (self.source / "body.md").write_text("New publisher version.\n")
        pending = self.package.refresh(self.source, publisher_revision=2)
        self.assertTrue(pending["reconciliation_required"])
        render = self.package.render("review", format="messages")
        self.assert_contract(RenderResult, render)
        self.assertEqual("My version.", render["output"][0]["content"])
        preview = self.package.preview_reconciliation()
        self.assert_contract(ReconciliationPreview, preview)
        self.assertTrue(preview["conflicts"])
        with self.assertRaises(PromptError):
            self.package.reconcile(expected_revision=preview["revision"])
        accepted = self.package.reconcile(expected_revision=preview["revision"], take="managed")
        self.assertFalse(accepted["reconciliation_required"])
        with self.assertRaises(PromptError) as error:
            self.package.save({"body.md": "Stale."}, expected_revision=edited["revision"])
        self.assertEqual("conflict", error.exception.code)
        pinned = self.package.resolve(revision=initial["revision"])
        self.assert_contract(ManagedResolution, pinned)
        self.assertEqual("Original.\n", pinned["snapshot"]["files"]["body.md"])
        self.assertIn(
            "Original.", self.package.render("review", revision=initial["revision"])["output"]
        )
        restored = self.package.restore(initial["revision"], expected_revision=accepted["revision"])
        self.assertEqual(accepted["baseline"], restored["baseline"])
        disabled = self.package.set_tracking(False, expected_revision=restored["revision"])
        self.assertFalse(disabled["tracking"])
        self.assertEqual(
            "tracking_stopped", self.package.refresh(self.source, publisher_revision=3)["outcome"]
        )
        release = self.package.release("original", revision=initial["revision"])
        self.assert_contract(ReleaseIdentity, release)
        self.assertIn("Original.", self.manager.render("review@original/review")["output"])
        for row in self.package.history():
            self.assert_contract(ManagedState, row)

    def test_candidate_validation_never_saves_even_on_failure(self):
        initial = self.seed()
        before = {str(p): p.read_bytes() for p in self.manager.store.home.rglob("*.json")}
        result = self.package.validate({"body.md": "Candidate.\n"})
        self.assert_contract(ValidationResult, result)
        self.assertEqual([], result["examples_passed"])
        self.assertNotEqual(initial["active_digest"], result["digest"])
        self.assertEqual(initial["revision"], result["revision"])
        with self.assertRaises(PromptError):
            self.package.validate({"body.md": "{{ missing }}"})
        self.assertEqual(
            before, {str(p): p.read_bytes() for p in self.manager.store.home.rglob("*.json")}
        )
        self.assertEqual(initial["active_digest"], self.package.validate()["digest"])
        manifest = self.package.resolve()["snapshot"]["pack"]
        manifest["examples"] = [
            {
                "name": "original-required",
                "prompt": "review",
                "inputs": {},
                "expect": {"contains": ["Original."]},
            }
        ]
        with self.assertRaises(PromptError) as error:
            self.package.validate({"pack.json": json.dumps(manifest), "body.md": "Changed."})
        self.assertEqual("example", error.exception.code)
        self.assertEqual(initial["revision"], self.package.status()["revision"])

    def test_comparison_uses_recorded_revisions_and_captured_inputs(self):
        initial = self.seed()
        edited = self.package.save({"body.md": "Changed.\n"}, expected_revision=initial["revision"])
        before = self.package.status()["revision"]
        result = self.package.compare(initial["revision"], inputs={}, prompt_id="review")
        self.assert_contract(ComparisonResult, result)
        self.assertTrue(result["changed"])
        self.assertIn("Changed.", result["render_diff"])
        self.assertEqual(initial["revision"], result["before"]["revision"])
        self.assertEqual(edited["revision"], result["after"]["revision"])
        self.assertFalse(self.package.compare(initial["revision"], initial["revision"])["changed"])
        self.assertEqual(before, self.package.status()["revision"])

    def test_export_is_self_contained_idempotent_and_never_overwrites(self):
        initial = self.seed()
        destination = self.root / "export.json"
        exported = self.package.export(destination)
        self.assert_contract(ExportResult, exported)
        self.assertEqual(initial["active_digest"], exported["digest"])
        self.assertEqual(exported, self.package.export(destination))
        changed = self.package.save({"body.md": "Changed."}, expected_revision=initial["revision"])
        original = destination.read_bytes()
        with self.assertRaises(PromptError) as error:
            self.package.export(destination)
        self.assertEqual("conflict", error.exception.code)
        self.assertEqual(original, destination.read_bytes())
        self.assertEqual(changed["revision"], self.package.status()["revision"])
        other = Manager(self.root / "other", packs=[])
        imported = other.import_local(destination, package_id="copy")
        shutil.rmtree(self.manager.store.home)
        shutil.rmtree(self.source)
        self.assertIn(
            "Original.", other.package("local", imported["id"]).render("review")["output"]
        )

    def test_filesystem_failures_are_normalized_without_masking_prompt_errors(self):
        with patch.object(self.manager, "managed_status", side_effect=PermissionError("Denied")):
            with self.assertRaises(PromptError) as error:
                self.package.status()
            self.assertEqual("io", error.exception.code)
        failure = PromptError("integrity", "Corrupt")
        with patch.object(self.manager, "managed_status", side_effect=failure):
            with self.assertRaises(PromptError) as error:
                self.package.status()
            self.assertIs(failure, error.exception)

    def test_descriptor_lookup_is_separate_and_read_only(self):
        with self.assertRaises(PromptError):
            self.manager.application_descriptor("missing")
        self.assertFalse(self.manager.store.home.exists())
        descriptor = self.manager.register_application("workspace", prompts={})
        self.assertEqual(descriptor, self.manager.application_descriptor("workspace"))
        self.assertEqual(descriptor, self.manager.application("workspace"))

    def test_cli_uses_explicit_package_workflows_and_preserves_result_schemas(self):
        initial = self.seed()

        def cli(*args):
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "prompt_manager",
                    "--home",
                    str(self.manager.store.home),
                    "managed",
                    *args,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            return json.loads(result.stdout)

        self.assertEqual(self.package.status(), cli("status", "publisher", "main"))
        candidate = cli("validate", "publisher", "main", "--files", '{"body.md":"Candidate."}')
        self.assert_contract(ValidationResult, candidate)
        self.assertEqual(initial["revision"], self.package.status()["revision"])
        comparison = cli("compare", "publisher", "main", initial["revision"])
        self.assertFalse(comparison["changed"])
        exported = cli("export", "publisher", "main", str(self.root / "cli-export.json"))
        self.assertEqual(initial["active_digest"], exported["digest"])
