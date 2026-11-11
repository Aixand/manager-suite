"""Old-store compatibility and native skills through the public Prompt SDK."""

import ast
import base64
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from prompt_manager import PackageLifecycle
import skill_manager
from skill_manager import Manager, SkillError
from skill_manager.codec import digest
from skill_manager.managed import Backend


class PromptIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manager = Manager(self.root / "store")

    def restore_legacy(self):
        fixture = json.loads(
            (Path(__file__).parent / "fixtures/pre_prompt_sdk_store.json").read_text()
        )
        for relative, value in fixture["records"].items():
            path = self.manager.home / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(value))
        return fixture

    def test_legacy_reads_and_repeated_publisher_preserve_records_and_releases(self):
        fixture = self.restore_legacy()
        before = {
            path: (self.manager.home / path).read_bytes() for path in fixture["records"]
        }
        pending = fixture["pending"]
        self.assertEqual(
            {key: value for key, value in pending.items() if key != "outcome"},
            self.manager.managed_status("publisher", "audit"),
        )
        history = self.manager.managed_history("publisher", "audit")
        self.assertEqual(
            [
                pending["revision"],
                fixture["custom"]["revision"],
                fixture["initial"]["revision"],
            ],
            [row["revision"] for row in history],
        )
        old = self.manager.resolve_managed(
            "publisher", "audit", revision=fixture["initial"]["revision"]
        )
        self.assertEqual(fixture["initial"]["active_digest"], old["digest"])
        release = fixture["release"]
        self.assertEqual(release, self.manager.resolve(release["reference"])["release"])
        self.assertEqual(
            release,
            self.manager.release_managed(
                "publisher", "audit", "v1", revision=fixture["initial"]["revision"]
            ),
        )
        sdk = self.manager._managed().lifecycle
        self.assertIsInstance(sdk, PackageLifecycle)
        upstream = Backend(self.manager).snapshot(pending["upstream"]["digest"])
        source = self.root / "csv-audit"
        source.mkdir()
        self.manager._materialize(
            source,
            upstream["manifest"]["directories"],
            {
                row["path"]: {
                    "content": upstream["contents"][row["path"]],
                    "executable": row["executable"],
                }
                for row in upstream["manifest"]["files"]
            },
        )
        observed = self.manager.ensure_registered(
            "publisher", source, skill_id="audit", publisher_revision=2
        )
        self.assertEqual(pending["revision"], observed["revision"])
        self.assertEqual(
            before, {path: (self.manager.home / path).read_bytes() for path in before}
        )

    def test_legacy_edits_review_restore_and_native_export(self):
        fixture = self.restore_legacy()
        review = self.manager.preview_reconciliation("publisher", "audit")
        applied = self.manager.reconcile(
            "publisher", "audit", expected_revision=review["revision"], take="managed"
        )
        self.assertFalse(applied["reconciliation_required"])
        self.assertNotIn("content_metadata", applied)
        self.assertEqual(
            b"User rules\n",
            self.manager.read_managed_file("publisher", "audit", "references/rules.md"),
        )
        destination = self.root / "csv-audit"
        exported = self.manager.export_managed("publisher", "audit", destination)
        self.assertEqual(applied["active_digest"], exported["digest"])
        self.assertEqual(
            "identical",
            self.manager.export_managed("publisher", "audit", destination)["status"],
        )
        (destination / "references/rules.md").write_bytes(b"Unrelated destination")
        with self.assertRaises(SkillError):
            self.manager.export_managed("publisher", "audit", destination)
        self.assertEqual(
            b"Unrelated destination", (destination / "references/rules.md").read_bytes()
        )
        restored = self.manager.rollback_managed(
            "publisher",
            "audit",
            fixture["initial"]["revision"],
            expected_revision=applied["revision"],
        )
        self.assertEqual(fixture["initial"]["active_digest"], restored["active_digest"])
        self.assertEqual(
            fixture["release"],
            self.manager.resolve(fixture["release"]["reference"])["release"],
        )
        with self.assertRaises(SkillError) as error:
            self.manager.save_managed(
                "publisher",
                "audit",
                {},
                expected_revision=fixture["pending"]["revision"],
            )
        self.assertEqual("conflict", error.exception.code)

    def test_delegates_policy_and_translates_sdk_errors(self):
        fixture = self.restore_legacy()
        with patch.object(
            PackageLifecycle,
            "reconcile",
            wraps=self.manager._managed().lifecycle.reconcile,
        ) as apply:
            self.manager.reconcile(
                "publisher",
                "audit",
                expected_revision=fixture["pending"]["revision"],
                take="managed",
            )
        self.assertEqual(1, apply.call_count)
        with self.assertRaises(SkillError) as error:
            self.manager.preview_reconciliation("publisher", "audit")
        self.assertEqual("conflict", error.exception.code)

    def test_skill_imports_only_public_prompt_sdk(self):
        package = Path(skill_manager.__file__).resolve().parent
        self.assertTrue((package / "_managed.py").is_file())
        for path in package.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module
                    and node.module.startswith("prompt_manager")
                ):
                    self.assertEqual("prompt_manager", node.module)
                if isinstance(node, ast.Import):
                    self.assertFalse(
                        any(
                            name.name.startswith("prompt_manager.")
                            for name in node.names
                        )
                    )


class GitHubFixture:
    def __init__(self):
        self.commit = "a" * 40
        self.content = "Original"
        self.relation_status = "ahead"

    def resolve(self, url, *, ref=None, path=None, track=None):
        return {
            "schema": "resource-sources/github-1",
            "kind": "github",
            "repository_id": "123",
            "repository": "example/resources",
            "url": "https://github.com/example/resources",
            "ref": ref or "main",
            "path": path or "",
            "commit": self.commit,
            "pinned": track is False,
        }

    def snapshot(self, source):
        raw = (
            "---\nname: review\ndescription: Review code\n---\n" + self.content
        ).encode()
        tree = {
            "schema": "resource-sources/tree-1",
            "directories": [],
            "files": {
                "skills/review/SKILL.md": {
                    "content_b64": base64.b64encode(raw).decode(),
                    "executable": False,
                }
            },
        }
        return deepcopy(tree), digest(tree)

    def relation(self, source, commit):
        return self.relation_status


class SourceAdapterTests(unittest.TestCase):
    def test_import_update_customization_pins_and_source_only_review(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = Manager(Path(directory) / "store")
            manager.source_client = GitHubFixture()
            discovery = manager.discover_source("https://github.com/example/resources")
            initial = manager.import_source(discovery["discovery_id"])["items"][0]
            publisher, identifier = initial["publisher_id"], initial["id"]
            manager.source_client.content = "Publisher v2"
            manager.source_client.commit = "b" * 40
            updated = manager.check_updates(publisher, identifier, force=True)
            self.assertEqual("updated", updated["outcome"])
            manager.save_managed(
                publisher,
                identifier,
                {"notes.md": {"content": b"My notes", "executable": False}},
                expected_revision=updated["revision"],
            )
            manager.source_client.content = "Publisher v3"
            manager.source_client.commit = "c" * 40
            pending = manager.check_updates(publisher, identifier, force=True)
            self.assertTrue(pending["reconciliation_required"])
            accepted = manager.reconcile(
                publisher,
                identifier,
                expected_revision=pending["revision"],
                take="managed",
            )
            self.assertFalse(accepted["reconciliation_required"])
            pinned = manager.source_settings(
                publisher,
                identifier,
                expected_revision=accepted["revision"],
                pinned=True,
            )
            with patch.object(
                manager.source_client,
                "resolve",
                side_effect=AssertionError("Unexpected fetch"),
            ):
                self.assertEqual(
                    "pinned",
                    manager.check_updates(publisher, identifier, force=True)["outcome"],
                )
            # Switching a source with identical native bytes still needs explicit acknowledgement.
            switched = manager.switch_source(
                publisher,
                identifier,
                expected_revision=pinned["revision"],
                ref="other",
                track=True,
            )
            self.assertTrue(switched["source_review"])
            review = manager.preview_reconciliation(publisher, identifier)
            manager.reconcile(
                publisher,
                identifier,
                expected_revision=review["revision"],
                take="managed",
            )
            self.assertFalse(
                manager.managed_status(publisher, identifier)["reconciliation_required"]
            )
            with self.assertRaises(SkillError) as error:
                manager.ensure_registered(
                    publisher, Path(directory) / "missing", skill_id=identifier
                )
            self.assertEqual("conflict", error.exception.code)
