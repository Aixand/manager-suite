"""Read and update a store produced by the implementation before relocation."""

import json
import tempfile
import unittest
from pathlib import Path

from prompt_manager import Manager

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "pre_refactor_store.json"


class StoredCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name) / "store"
        self.fixture = json.loads(FIXTURE.read_text())
        for path, record in self.fixture["files"].items():
            target = self.home / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(record), encoding="utf-8")
        self.manager = Manager(self.home, packs=[])

    def test_existing_release_and_managed_renders_retain_exact_results(self):
        before = {str(p): p.read_bytes() for p in self.home.rglob("*.json")}
        self.assertEqual(
            self.fixture["expected"]["release_render"],
            self.manager.render("review@1/review", self.fixture["inputs"], format="messages"),
        )
        self.assertEqual(
            self.fixture["expected"]["managed_render"],
            self.manager.render_managed(
                "publisher", "default", "review", self.fixture["inputs"], format="messages"
            ),
        )
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.home.rglob("*.json")})

    def test_captured_dependencies_render_without_the_original_release_objects(self):
        reference = json.loads((self.home / "releases/common/1.json").read_text())
        (self.home / "releases/common/1.json").unlink()
        (self.home / "objects" / (reference["digest"][7:] + ".json")).unlink()
        self.assertEqual(
            self.fixture["expected"]["managed_render"],
            self.manager.render_managed(
                "publisher", "default", "review", self.fixture["inputs"], format="messages"
            ),
        )

    def test_store_checker_accepts_embedded_dependencies_without_original_release_objects(self):
        reference = json.loads((self.home / "releases/common/1.json").read_text())
        (self.home / "releases/common/1.json").unlink()
        (self.home / "objects" / (reference["digest"][7:] + ".json")).unlink()
        before = {str(path): path.read_bytes() for path in self.home.rglob("*.json")}
        checked = self.manager.check_store(include_external=False)
        self.assertTrue(checked["ok"], checked)
        self.assertEqual(
            before, {str(path): path.read_bytes() for path in self.home.rglob("*.json")}
        )

    def test_independent_copy_keeps_captured_dependencies_without_original_releases(self):
        copy = self.manager.package("publisher", "default").duplicate("independent")
        reference = json.loads((self.home / "releases/common/1.json").read_text())
        (self.home / "releases/common/1.json").unlink()
        (self.home / "objects" / (reference["digest"][7:] + ".json")).unlink()
        rendered = self.manager.package("local", copy["id"]).render(
            "review",
            self.fixture["inputs"],
            format="messages",
        )
        self.assertEqual(self.fixture["expected"]["managed_render"]["output"], rendered["output"])
        self.assertFalse(copy["tracking"])
        self.assertFalse(copy["reconciliation_required"])
        self.assertTrue(self.manager.check_store(include_external=False)["ok"])

    def test_manual_review_of_existing_state_preserves_content_and_history(self):
        expected = self.fixture["expected"]
        current = self.manager.managed_status("publisher", "default")
        self.assertEqual(expected["pending_revision"], current["revision"])
        result = self.manager.render_application("consumer", "review", self.fixture["inputs"])
        self.assertIn("My review", result["output"])
        kept = self.manager.reconcile(
            "publisher", "default", expected_revision=current["revision"], take="managed"
        )
        self.assertFalse(kept["reconciliation_required"])
        self.assertEqual(current["active_digest"], kept["active_digest"])
        pinned = self.manager.render_managed(
            "publisher",
            "default",
            "review",
            self.fixture["inputs"],
            revision=expected["initial_revision"],
            format="messages",
        )
        self.assertEqual(expected["release_render"]["output"], pinned["output"])
        self.assertEqual(4, len(self.manager.managed_history("publisher", "default")))
