"""Format-independent lifecycle qualification using only the public SDK."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from support.file_backend import FileBackend, sample

from prompt_manager import PACKAGE_LIFECYCLE_VERSION, Manager, PackageLifecycle, PromptError


class PackageLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.backend = FileBackend(self.root / "store")
        self.sdk = PackageLifecycle(self.backend)
        self.source = sample()

    def ensure(self, revision):
        return self.sdk.ensure_registered(
            "publisher", "main", self.source, publisher_revision=revision
        )

    def test_prompt_backend_uses_same_sdk_and_preserves_store_contracts(self):
        manager = Manager(self.root / "prompts", packs=[])
        lifecycle = manager.lifecycle()
        self.assertFalse(manager.store.home.exists())
        created = manager.create_managed("review", content="Original.\n")
        original = manager.resolve_managed("local", "review")
        self.assertEqual(original["snapshot"], lifecycle.resolve("local", "review")["snapshot"])
        changed = lifecycle.save(
            "local", "review", {"body.md": "Edited.\n"}, expected_revision=created["revision"]
        )
        self.assertEqual(
            "[user]\nEdited.\n", manager.package("local", "review").render("review")["output"]
        )
        released = lifecycle.release("local", "review", "v1", revision=created["revision"])
        self.assertEqual(created["active_digest"], released["digest"])
        self.assertEqual(original["snapshot"], manager.store.load("review@v1")[0])
        self.assertEqual(changed["revision"], manager.managed_status("local", "review")["revision"])
        self.assertTrue(manager.check_store()["ok"])
        archive = self.root / "prompts.zip"
        manager.backup_store(archive)
        restored = Manager(self.root / "restored", packs=[])
        restored.restore_store(archive)
        self.assertEqual(
            lifecycle.resolve("local", "review"), restored.lifecycle().resolve("local", "review")
        )

    def test_owned_bytes_structure_and_restart(self):
        self.assertEqual(1, PACKAGE_LIFECYCLE_VERSION)
        self.assertEqual([], self.sdk.catalog())
        self.assertFalse(self.backend.home.exists())
        initial = self.ensure(1)
        self.source.clear()
        restarted = PackageLifecycle(FileBackend(self.backend.home))
        resolved = restarted.resolve("publisher", "main")
        self.assertEqual(sample(), resolved["snapshot"])
        self.assertEqual(initial["active_digest"], resolved["digest"])
        self.assertEqual(
            [],
            restarted.compare("publisher", "main", initial["revision"], initial["revision"])[
                "changes"
            ],
        )

    def test_acquisition_records_are_guarded_and_validated_before_publication(self):
        initial = self.ensure(1)
        record = self.sdk.record("publisher", "main")
        self.assertNotIn("customized", record)
        invalid = deepcopy(record)
        invalid["tracking"] = "invalid"
        before = {path: path.read_bytes() for path in self.backend.home.rglob("*.json")}
        with self.backend.lock():
            with self.assertRaises(PromptError):
                self.sdk.commit_record(invalid, expected_revision=initial["revision"])
        self.assertEqual(
            before, {path: path.read_bytes() for path in self.backend.home.rglob("*.json")}
        )
        record["label"] = "Acquired content"
        with self.backend.lock():
            committed = self.sdk.commit_record(record, expected_revision=initial["revision"])
            with self.assertRaises(PromptError) as error:
                self.sdk.commit_record(record, expected_revision=initial["revision"])
        self.assertEqual("conflict", error.exception.code)
        self.assertEqual(committed["revision"], self.sdk.status("publisher", "main")["revision"])

    def test_ordering_customization_reconciliation_and_history(self):
        initial = self.ensure(1)
        local = self.sdk.save(
            "publisher",
            "main",
            {"instructions.md": {"text": "ours\nmiddle\nlast\n", "executable": False}},
            expected_revision=initial["revision"],
        )
        self.source["files"]["instructions.md"]["text"] = "first\nmiddle\ntheirs\n"
        pending = self.ensure(2)
        self.assertTrue(pending["reconciliation_required"])
        self.assertEqual("ignored_older", self.ensure(1)["outcome"])
        review = self.sdk.preview_reconciliation("publisher", "main")
        self.assertEqual([], review["conflicts"])
        applied = self.sdk.reconcile("publisher", "main", expected_revision=review["revision"])
        self.assertFalse(applied["reconciliation_required"])
        self.assertEqual(
            "ours\nmiddle\ntheirs\n",
            self.sdk.resolve("publisher", "main")["snapshot"]["files"]["instructions.md"]["text"],
        )
        self.assertEqual(
            local["active_digest"],
            self.sdk.resolve("publisher", "main", revision=local["revision"])["digest"],
        )
        restored = self.sdk.rollback(
            "publisher", "main", initial["revision"], expected_revision=applied["revision"]
        )
        self.assertEqual(initial["active_digest"], restored["active_digest"])
        self.assertTrue(restored["hold"])
        self.assertEqual(5, len(self.sdk.history("publisher", "main")))

    def test_binary_conflicts_never_text_merge_and_flags_are_independent(self):
        self.source["files"]["image.bin"]["content_b64"] = "AAAABBBBCCCC"
        initial = self.ensure(1)
        self.sdk.save(
            "publisher",
            "main",
            {"image.bin": {"content_b64": "DDDDBBBBCCCC", "executable": True}},
            expected_revision=initial["revision"],
        )
        self.source["files"]["image.bin"]["content_b64"] = "AAAABBBBEEEE"
        pending = self.ensure(2)
        with patch(
            "prompt_manager.managed.reconciliation.merge_text",
            side_effect=AssertionError("Binary content cannot be text merged"),
        ):
            preview = self.sdk.preview_reconciliation("publisher", "main")
        self.assertEqual(
            ["/files/image.bin/content_b64"],
            [conflict["path"] for conflict in preview["conflicts"]],
        )
        self.assertTrue(preview["candidate"]["files"]["image.bin"]["executable"])
        applied = self.sdk.reconcile(
            "publisher",
            "main",
            expected_revision=pending["revision"],
            resolutions={"/files/image.bin/content_b64": {"take": "publisher"}},
        )
        self.assertFalse(applied["reconciliation_required"])

    def test_reused_revision_stale_edit_invalid_candidate_and_failed_write(self):
        initial = self.ensure(1)
        with self.assertRaises(PromptError):
            self.sdk.validate("publisher", "main", {"instructions.md": None})
        self.assertEqual(initial["revision"], self.sdk.status("publisher", "main")["revision"])
        changed = self.sdk.save(
            "publisher", "main", {"image.bin": None}, expected_revision=initial["revision"]
        )
        with self.assertRaises(PromptError):
            self.sdk.save("publisher", "main", {}, expected_revision=initial["revision"])
        self.source["files"]["instructions.md"]["text"] = "Changed publisher"
        with self.assertRaises(PromptError):
            self.ensure(1)
        self.assertEqual(changed["active_digest"], self.sdk.resolve("publisher", "main")["digest"])
        current = self.sdk.status("publisher", "main")
        with patch.object(self.backend, "store", side_effect=OSError("disk full")):
            with self.assertRaises(PromptError):
                self.sdk.save(
                    "publisher", "main", {"run.py": None}, expected_revision=current["revision"]
                )
        self.assertEqual(current["revision"], self.sdk.status("publisher", "main")["revision"])

    def test_effective_registration_failure_tracking_and_organization(self):
        initial = self.ensure(1)
        result = self.sdk.register("publisher", "main", {}, publisher_revision=2)
        self.assertEqual("refresh_failed", result["status"]["outcome"])
        self.assertEqual(sample(), result["snapshot"])
        status = self.sdk.status("publisher", "main")
        status = self.sdk.set_tags(
            "publisher", "main", [" BINARY "], expected_revision=status["revision"]
        )
        status = self.sdk.rename(
            "publisher", "main", "My files", expected_revision=status["revision"]
        )
        found = self.sdk.search("binary", tags=["binary"], publisher_id="publisher")
        self.assertTrue(found["complete"])
        self.assertEqual("My files", found["items"][0]["label"])
        status = self.sdk.set_archived(
            "publisher", "main", True, expected_revision=status["revision"]
        )
        self.assertEqual([], self.sdk.catalog())
        self.assertEqual(1, len(self.sdk.catalog(include_archived=True)))
        status = self.sdk.set_tracking(
            "publisher", "main", False, expected_revision=status["revision"]
        )
        self.assertEqual("tracking_stopped", self.ensure(2)["outcome"])
        self.assertEqual(initial["active_digest"], status["active_digest"])

    def test_create_only_and_legacy_adoption(self):
        created = self.sdk.create("local", "copy", self.source)
        self.assertFalse(created["tracking"])
        with self.assertRaises(PromptError):
            self.sdk.create("local", "copy", self.source)
        legacy = deepcopy(self.source)
        legacy["files"]["instructions.md"]["text"] = "Customized"
        adopted = self.sdk.adopt_legacy(
            "publisher", "main", legacy, self.source, publisher_revision=1
        )
        self.assertFalse(adopted["baseline_known"])
        self.assertTrue(self.sdk.preview_reconciliation("publisher", "main")["conflicts"])

    def test_missing_delivery_is_explicit(self):
        self.ensure(1)
        with self.assertRaises(PromptError) as error:
            self.sdk.export("publisher", "main", self.root / "export")
        self.assertEqual("unsupported", error.exception.code)

    def test_bounded_file_and_review_views(self):
        initial = self.ensure(1)
        view = self.sdk.view("publisher", "main", path="/files/image.bin")
        binary = view["sides"]["managed"]
        self.assertTrue(binary["binary"])
        self.assertEqual(2, binary["size"])
        self.assertNotIn("content_b64", binary)
        text = self.sdk.view("publisher", "main", path="/files/instructions.md", limit=5)
        self.assertEqual("first", text["sides"]["managed"]["text"])
        self.assertEqual(5, text["sides"]["managed"]["next_offset"])
        self.sdk.save(
            "publisher",
            "main",
            {"image.bin": {"content_b64": "AAE=", "executable": True}},
            expected_revision=initial["revision"],
        )
        self.source["files"]["image.bin"]["content_b64"] = "AAI="
        pending = self.ensure(2)
        review = self.sdk.view(
            "publisher",
            "main",
            review=True,
            path="/files/image.bin",
            expected_revision=pending["revision"],
        )
        self.assertEqual(1, review["unresolved_count"])
        self.assertTrue(review["selected_conflicts"][0]["sides"]["managed"]["binary"])
        with self.assertRaises(PromptError):
            self.sdk.view("publisher", "main", expected_revision=initial["revision"])

    def test_native_delivery_receives_exact_historical_snapshot(self):
        from prompt_manager.codec import digest
        from prompt_manager.storage.objects import publish_immutable

        class Delivery(FileBackend):
            def publish(self, snapshot, version, status):
                record = {"snapshot": snapshot, "digest": status["active_digest"]}
                publish_immutable(self.home / "releases" / (version + ".json"), record)
                return {"version": version, "digest": record["digest"]}

            def export(self, snapshot, destination, status):
                publish_immutable(Path(destination), snapshot)
                return {"digest": digest(snapshot)}

        sdk = PackageLifecycle(Delivery(self.backend.home))
        initial = sdk.create("local", "copy", sample())
        sdk.save("local", "copy", {"image.bin": None}, expected_revision=initial["revision"])
        released = sdk.release("local", "copy", "v1", revision=initial["revision"])
        self.assertEqual(initial["active_digest"], released["digest"])
        self.assertEqual(released, sdk.release("local", "copy", "v1", revision=initial["revision"]))
        with self.assertRaises(PromptError):
            sdk.release("local", "copy", "v1")
        destination = self.root / "export.json"
        sdk.export("local", "copy", destination, revision=initial["revision"])
        from prompt_manager.storage.files import read_json

        self.assertEqual(sample(), read_json(destination))
        before = destination.read_bytes()
        with self.assertRaises(PromptError):
            sdk.export("local", "copy", destination)
        self.assertEqual(before, destination.read_bytes())

    def test_only_prompt_manager_is_needed_in_isolation(self):
        root = Path(__file__).resolve().parents[2]
        shutil.copytree(
            root / "src/prompt_manager",
            self.root / "prompt_manager",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        shutil.copy(root / "tests/support/file_backend.py", self.root / "file_backend.py")
        script = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
def audit(event, args):
    if event in {"socket.connect", "socket.getaddrinfo"}:
        raise AssertionError("Network is forbidden")
sys.addaudithook(audit)
from prompt_manager import PackageLifecycle
from file_backend import FileBackend, sample
sdk = PackageLifecycle(FileBackend(Path(sys.argv[1]) / "isolated-store"))
created = sdk.create("local", "files", sample())
assert sdk.resolve("local", "files")["snapshot"] == sample()
assert not any(name.split(".")[0] in {
    "skill_manager", "resource_sources", "aixand_platform", "agent_manager"
} for name in sys.modules)
print("isolated")
"""
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-c", script, str(self.root)],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("isolated", result.stdout.strip())
