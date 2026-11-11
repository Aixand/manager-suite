"""Guarded tags and body-free metadata search across managed packages."""

import inspect
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_manager import (
    ManagedSearchEntry,
    ManagedSearchResult,
    Manager,
    Package,
    PromptError,
)
from prompt_manager.codec import digest
from prompt_manager.managed.registration import Backend
from prompt_manager.storage.files import atomic_json

FIXTURE = Path(__file__).parents[1] / "fixtures/pre_refactor_store.json"


class SearchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manager = Manager(self.root / "store", packs=[])

    def create(self, publisher, identifier, description="", *, body="Secret body text"):
        source = self.root / (publisher + "-" + identifier)
        source.mkdir()
        manifest = {
            "schema": "prompt-manager/pack-1",
            "name": "pack-" + identifier,
            "description": description,
            "sections": {"body": {"file": "body.md"}},
            "prompts": {"review": {"sections": [{"id": "body", "section": "body"}]}},
        }
        (source / "pack.json").write_text(json.dumps(manifest))
        (source / "body.md").write_text(body)
        package = self.manager.package(publisher, identifier)
        package.refresh(source, publisher_revision=1)
        return package, source

    def records(self):
        return {str(path): path.read_bytes() for path in self.manager.store.home.rglob("*.json")}

    def ids(self, result):
        self.assertTrue(ManagedSearchResult.__required_keys__ <= result.keys())
        for item in result["items"]:
            self.assertTrue(ManagedSearchEntry.__required_keys__ <= item.keys())
        json.dumps(result, allow_nan=False)
        return [(row["publisher_id"], row["package_id"]) for row in result["items"]]

    def test_tag_updates_are_guarded_normalized_idempotent_and_do_not_read_bodies(self):
        package, _ = self.create("local", "alpha", "Python reviews")
        first = package.status()
        guard = inspect.signature(Package.set_tags).parameters["expected_revision"]
        self.assertEqual(inspect.Parameter.KEYWORD_ONLY, guard.kind)
        self.assertIs(inspect.Parameter.empty, guard.default)
        with patch.object(Backend, "snapshot", side_effect=AssertionError("Body read")):
            tagged = package.set_tags(
                [" QA ", "python", "qa", "Straße"], expected_revision=first["revision"]
            )
            self.assertEqual(["python", "qa", "strasse"], tagged["tags"])
            self.assertEqual(first["active_digest"], tagged["active_digest"])
            self.assertFalse(tagged["customized"])
            repeated = package.set_tags(
                ["STRASSE", "PYTHON", "qa"], expected_revision=tagged["revision"]
            )
            self.assertEqual(tagged["revision"], repeated["revision"])
            self.assertEqual(
                [("local", "alpha")], self.ids(self.manager.search_managed(tags=[" QA", "python"]))
            )
            with self.assertRaises(PromptError) as stale:
                package.set_tags(["qa"], expected_revision=first["revision"])
            self.assertEqual("conflict", stale.exception.code)
            cleared = package.set_tags([], expected_revision=tagged["revision"])
            self.assertNotIn("tags", cleared)
        self.assertTrue(self.manager.check_store()["ok"])

    def test_invalid_tag_inputs_leave_all_records_unchanged(self):
        package, _ = self.create("local", "alpha")
        first = package.status()
        before = self.records()
        for tags in ("tag", None, [None], [" "], ["x" * 65], ["x"] * 33, ["\ud800"]):
            with self.subTest(tags=repr(tags)), self.assertRaises(PromptError):
                package.set_tags(tags, expected_revision=first["revision"])
        self.assertEqual(before, self.records())

    def test_query_matches_metadata_fields_casefolded_and_terms_are_anded(self):
        alpha, _ = self.create("tool", "alpha", "Python parser guidance")
        beta, _ = self.create("local", "beta", "Writing summaries")
        named = alpha.rename("Careful review", expected_revision=alpha.status()["revision"])
        alpha.set_tags(["Engineering"], expected_revision=named["revision"])
        beta.set_tags(["writing"], expected_revision=beta.status()["revision"])
        before = self.records()
        with patch.object(Backend, "snapshot", side_effect=AssertionError("Body read")):
            for query in (
                "CAREFUL",
                "alpha",
                "pack-alpha",
                "parser",
                "engineering",
                "tool parser",
                "review PYTHON",
            ):
                self.assertEqual(
                    [("tool", "alpha")], self.ids(self.manager.search_managed(query)), query
                )
            self.assertEqual([], self.ids(self.manager.search_managed("Secret body")))
            self.assertEqual([], self.ids(self.manager.search_managed("Python writing")))
            self.assertEqual(
                [("local", "beta"), ("tool", "alpha")], self.ids(self.manager.search_managed())
            )
            self.assertEqual(
                [], self.ids(self.manager.search_managed(tags=["engineering", "writing"]))
            )
            self.assertEqual(
                [("tool", "alpha")], self.ids(self.manager.search_managed(publisher_id="tool"))
            )
        self.assertEqual(before, self.records())

    def test_archived_customized_and_pending_filters_are_independent(self):
        alpha, source = self.create("tool", "alpha", "Original metadata")
        beta, _ = self.create("local", "beta", "Writing")
        alpha.save({"body.md": "User edited."}, expected_revision=alpha.status()["revision"])
        (source / "body.md").write_text("Publisher changed.")
        alpha.refresh(source, publisher_revision=2)
        beta.archive(expected_revision=beta.status()["revision"])
        self.assertEqual(
            [("tool", "alpha")],
            self.ids(self.manager.search_managed(customized=True, reconciliation_required=True)),
        )
        self.assertEqual([], self.ids(self.manager.search_managed(customized=False)))
        self.assertEqual(
            [("local", "beta")],
            self.ids(
                self.manager.search_managed(
                    archived=True, customized=False, reconciliation_required=False
                )
            ),
        )
        self.assertEqual(2, len(self.manager.search_managed(archived=None)["items"]))
        self.assertEqual([], self.ids(self.manager.search_managed(publisher_id="local")))

    def test_descriptive_metadata_follows_active_content_not_pending_publisher_content(self):
        package, source = self.create("tool", "alpha", "First description")
        first = package.status()
        package.set_tags(["review"], expected_revision=first["revision"])
        manifest = json.loads((source / "pack.json").read_text())
        manifest["description"] = "Second description"
        (source / "pack.json").write_text(json.dumps(manifest))
        accepted = package.refresh(source, publisher_revision=2)
        self.assertEqual("Second description", accepted["content_metadata"]["description"])
        package.save({"body.md": "User edit."}, expected_revision=accepted["revision"])
        manifest["description"] = "Pending description"
        (source / "pack.json").write_text(json.dumps(manifest))
        pending = package.refresh(source, publisher_revision=3)
        self.assertEqual("Second description", pending["content_metadata"]["description"])
        self.assertEqual([], self.ids(self.manager.search_managed("Pending")))
        self.assertEqual(
            [("tool", "alpha")],
            self.ids(self.manager.search_managed("Second", reconciliation_required=True)),
        )
        adopted = package.reconcile(expected_revision=pending["revision"], take="publisher")
        self.assertEqual("Pending description", adopted["content_metadata"]["description"])
        self.assertEqual(["review"], adopted["tags"])
        restored = package.restore(first["revision"], expected_revision=adopted["revision"])
        self.assertEqual("First description", restored["content_metadata"]["description"])
        self.assertEqual(restored["active_digest"], restored["content_metadata"]["digest"])
        self.assertEqual(["review"], restored["tags"])
        self.assertTrue(self.manager.check_store()["ok"])

    def test_manifest_edits_and_legacy_adoption_capture_the_effective_description(self):
        package, source = self.create("tool", "alpha", "Publisher description")
        snapshot = package.resolve()["snapshot"]
        snapshot["pack"]["description"] = "User description"
        changed = package.save(
            {"pack.json": json.dumps(snapshot["pack"])},
            expected_revision=package.status()["revision"],
        )
        self.assertEqual("User description", changed["content_metadata"]["description"])
        legacy = self.root / "legacy"
        legacy.mkdir()
        (legacy / "pack.json").write_text(json.dumps(snapshot["pack"]))
        (legacy / "body.md").write_text("Legacy user content.")
        adopted = self.manager.adopt_legacy(
            "legacy", "default", legacy, source, publisher_revision=1
        )
        self.assertEqual("User description", adopted["content_metadata"]["description"])
        self.assertFalse(adopted["baseline_known"])
        self.assertEqual(
            [("legacy", "default"), ("tool", "alpha")],
            self.ids(self.manager.search_managed("User description")),
        )

    def test_legacy_records_are_partial_without_blob_reads_until_explicit_metadata_refresh(self):
        fixture = json.loads(FIXTURE.read_text())
        for relative, record in fixture["files"].items():
            path = self.manager.store.home / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(record))
        before = self.records()
        with patch.object(Backend, "snapshot", side_effect=AssertionError("Body read")):
            result = self.manager.search_managed("not-in-metadata")
        self.assertEqual([], result["items"])
        self.assertFalse(result["metadata_complete"])
        self.assertFalse(result["complete"])
        self.assertTrue(result["metadata_missing"])
        self.assertEqual(before, self.records())
        reference = result["metadata_missing"][0]
        package = self.manager.package(reference["publisher_id"], reference["package_id"])
        old = package.status()
        refreshed = package.refresh_metadata(expected_revision=reference["revision"])
        self.assertEqual(old["active_digest"], refreshed["active_digest"])
        self.assertEqual(old["baseline"], refreshed["baseline"])
        self.assertEqual(old["upstream"], refreshed["upstream"])
        self.assertEqual(old["tracking"], refreshed["tracking"])
        self.assertNotEqual(old["revision"], refreshed["revision"])
        self.assertTrue(self.manager.search_managed()["metadata_complete"])
        self.assertEqual(
            refreshed["revision"],
            package.refresh_metadata(expected_revision=refreshed["revision"])["revision"],
        )
        with self.assertRaises(PromptError):
            package.refresh_metadata(expected_revision=old["revision"])

    def test_corrupt_entries_are_isolated_and_query_validation_is_read_only(self):
        self.create("tool", "alpha", "Known description")
        broken = self.manager.store.home / "managed/tool/broken.json"
        broken.write_text("{")
        with patch.object(Backend, "snapshot", side_effect=AssertionError("Body read")):
            result = self.manager.search_managed()
        self.assertEqual([("tool", "alpha")], self.ids(result))
        self.assertFalse(result["complete"])
        self.assertEqual("json", result["issues"][0]["error"]["code"])
        self.assertEqual([], self.manager.search_managed(publisher_id="local")["issues"])
        before = self.records()
        for options in (
            {"query": None},
            {"tags": "review"},
            {"archived": 1},
            {"customized": "yes"},
            {"reconciliation_required": []},
            {"publisher_id": "../escape"},
        ):
            with self.subTest(options=options), self.assertRaises(PromptError):
                self.manager.search_managed(**options)
        self.assertEqual(before, self.records())

    def test_copies_and_full_backup_preserve_independent_tags_and_search_metadata(self):
        package, _ = self.create("tool", "alpha", "Accepted description")
        tagged = package.set_tags(["review"], expected_revision=package.status()["revision"])
        copied = package.duplicate("copy")
        self.assertEqual(["review"], copied["tags"])
        self.assertEqual("copy", copied["content_metadata"]["name"])
        copy = self.manager.package("local", "copy")
        copy.set_tags(["personal"], expected_revision=copied["revision"])
        self.assertEqual(tagged["tags"], package.status()["tags"])
        before = self.manager.search_managed("description")
        archive = self.root / "backup.zip"
        self.manager.backup_store(archive)
        restored = Manager(self.root / "restored", packs=[])
        restored.restore_store(archive)
        self.assertEqual(before["items"], restored.search_managed("description")["items"])
        self.assertTrue(restored.check_store()["ok"])

    def test_integrity_check_verifies_cached_description_against_content(self):
        package, _ = self.create("tool", "alpha", "Actual description")
        row = self.manager._managed().load("tool", "alpha")
        old_revision = row["revision"]
        row["content_metadata"]["description"] = "Incorrect cached description"
        row["sequence"] += 1
        row["previous"] = old_revision
        row["revision"] = digest({key: value for key, value in row.items() if key != "revision"})
        atomic_json(self.manager.store.home / "managed/tool/alpha.json", row)
        atomic_json(
            self.manager.store.home
            / "managed-history/tool/alpha"
            / (row["revision"][7:] + ".json"),
            row,
        )
        checked = self.manager.check_store()
        self.assertFalse(checked["ok"])
        self.assertTrue(
            any(item["code"] == "content_metadata_mismatch" for item in checked["findings"])
        )
        self.assertEqual(
            "Incorrect cached description", package.status()["content_metadata"]["description"]
        )

    def test_directory_access_failure_is_not_reported_as_a_complete_empty_library(self):
        self.create("tool", "alpha")
        original = Path.iterdir

        def denied(path):
            if path == self.manager.store.home / "managed":
                raise PermissionError("Cannot enumerate managed records")
            return original(path)

        with patch.object(Path, "iterdir", denied):
            with self.assertRaises(PromptError) as error:
                self.manager.search_managed()
        self.assertEqual("io", error.exception.code)

    def test_publisher_filter_does_not_enumerate_unrelated_publisher_directories(self):
        self.create("tool", "alpha")
        self.create("local", "beta")
        original = Path.iterdir

        def denied(path):
            if path == self.manager.store.home / "managed/tool":
                raise PermissionError("Unrelated publisher is inaccessible")
            return original(path)

        with patch.object(Path, "iterdir", denied):
            self.assertEqual(
                [("local", "beta")], self.ids(self.manager.search_managed(publisher_id="local"))
            )
            with self.assertRaises(PromptError):
                self.manager.search_managed()

    def test_absent_store_search_does_not_create_storage(self):
        result = self.manager.search_managed()
        self.assertEqual([], self.ids(result))
        self.assertTrue(result["complete"])
        self.assertFalse(self.manager.store.home.exists())

    def test_cli_tags_search_filters_and_refresh_metadata_match_sdk(self):
        package, _ = self.create("tool", "alpha", "Python description")

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

        tagged = cli(
            "set-tags",
            "tool",
            "alpha",
            "--tag",
            "Python",
            "--tag",
            "Review",
            "--expected-revision",
            package.status()["revision"],
        )
        self.assertEqual(["python", "review"], tagged["tags"])
        self.assertEqual(
            self.manager.search_managed("PYTHON", tags=["review"], publisher_id="tool"),
            cli("search", "PYTHON", "--tag", "review", "--publisher", "tool"),
        )
        archived = package.archive(expected_revision=tagged["revision"])
        self.assertEqual([], cli("search")["items"])
        self.assertEqual(
            [("tool", "alpha")],
            self.ids(cli("search", "--archived", "--unmodified", "--no-pending-review")),
        )
        self.assertEqual([("tool", "alpha")], self.ids(cli("search", "--include-archived")))
        refreshed = cli(
            "refresh-metadata", "tool", "alpha", "--expected-revision", archived["revision"]
        )
        self.assertEqual(archived["revision"], refreshed["revision"])
        cleared = cli(
            "set-tags", "tool", "alpha", "--clear", "--expected-revision", refreshed["revision"]
        )
        self.assertNotIn("tags", cleared)
