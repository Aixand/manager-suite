import ast
import base64
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import skill_manager
from skill_manager import Manager, SkillError
from skill_manager.github_source import GitHub


class OfflineSourceTests(unittest.TestCase):
    def test_legacy_source_and_completed_job_records_remain_readable_without_fetches(
        self,
    ):
        fixture = json.loads(
            (
                Path(__file__).parent / "fixtures/pre_github_source_store.json"
            ).read_text()
        )
        with tempfile.TemporaryDirectory() as temporary:
            manager = Manager(Path(temporary) / "store")
            for relative, value in fixture["records"].items():
                path = manager.home / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(base64.b64decode(value))
            before = {
                path: path.read_bytes()
                for path in manager.home.rglob("*")
                if path.is_file()
            }
            publisher, identifier = fixture["publisher"], fixture["identifier"]
            with patch(
                "skill_manager.sources.GitHub",
                side_effect=AssertionError("Unexpected fetch"),
            ):
                status = manager.managed_status(publisher, identifier)
                self.assertEqual(fixture["pending"]["revision"], status["revision"])
                self.assertTrue(status["reconciliation_required"])
                self.assertEqual(
                    b"My notes\n",
                    manager.read_managed_file(publisher, identifier, "notes.md"),
                )
                self.assertEqual(
                    status["source"],
                    manager.source_status(publisher, identifier)["source"],
                )
                self.assertEqual(
                    fixture["release"],
                    manager.resolve(fixture["release"]["reference"])["release"],
                )
                self.assertEqual(
                    fixture["job"], manager.source_job(fixture["job"]["id"])
                )
                self.assertEqual([fixture["job"]], manager.source_jobs())
            self.assertEqual(before, {path: path.read_bytes() for path in before})
            # Acceptance and history use the same native identities after upgrading.
            review = manager.preview_reconciliation(publisher, identifier)
            kept = manager.reconcile(
                publisher,
                identifier,
                expected_revision=review["revision"],
                take="managed",
            )
            self.assertFalse(kept["reconciliation_required"])
            self.assertEqual(
                b"My notes\n",
                manager.read_managed_file(publisher, identifier, "notes.md"),
            )

    def test_runtime_has_no_resource_sources_imports(self):
        for path in Path(skill_manager.__file__).parent.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                names = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else (
                        [node.module]
                        if isinstance(node, ast.ImportFrom) and node.module
                        else []
                    )
                )
                self.assertFalse(
                    any(name.split(".")[0] == "resource_sources" for name in names),
                    str(path),
                )

    def test_missing_optional_dependency_has_actionable_error(self):
        import builtins

        original = builtins.__import__

        def without_sdk(name, *args, **kwargs):
            if name == "github_source":
                raise ModuleNotFoundError("GitHub source is absent", name=name)
            return original(name, *args, **kwargs)

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("builtins.__import__", side_effect=without_sdk),
        ):
            manager = Manager(Path(temporary) / "store")
            created = manager.create_managed(
                "local", content="Instructions", description="Local skill"
            )
            self.assertEqual(
                created["active_digest"],
                manager.resolve_managed("local", "local")["digest"],
            )
            with self.assertRaises(SkillError) as error:
                manager.discover_source("https://github.com/example/resources")
            self.assertEqual("unsupported", error.exception.code)
            self.assertIn("[github]", str(error.exception))


@unittest.skipUnless(
    importlib.util.find_spec("github_source"), "Optional GitHub SDK is not installed"
)
class SharedBridgeTests(unittest.TestCase):
    def test_generic_snapshot_translation_preserves_legacy_identity_and_package_selection(
        self,
    ):
        source = {
            "schema": "github-source/source-1",
            "kind": "github",
            "repository_id": "123",
            "repository": "example/resources",
            "url": "https://github.com/example/resources",
            "ref": "main",
            "path": "skills/review/SKILL.md",
            "commit": "a" * 40,
            "reference_kind": "branch",
            "tree_sha": "b" * 40,
        }
        tree = {
            "schema": "github-source/tree-1",
            "directories": ["skills", "skills/review"],
            "files": {
                "skills/review/SKILL.md": {"content_b64": "", "executable": False}
            },
        }
        bridge = GitHub()
        with patch.object(bridge.client, "resolve", return_value=dict(source)):
            legacy = bridge.resolve(source["url"], ref="main", path=source["path"])
        self.assertEqual("resource-sources/github-1", legacy["schema"])
        self.assertFalse(legacy["pinned"])
        self.assertNotIn("reference_kind", legacy)
        with patch.object(bridge.client, "fetch", return_value={"tree": tree}) as fetch:
            captured, identity = bridge.snapshot(legacy)
        self.assertEqual("skills/review", fetch.call_args.args[0]["path"])
        self.assertEqual("resource-sources/tree-1", captured["schema"])
        from skill_manager._source_records import digest

        self.assertEqual(digest(captured), identity)

    def test_commit_sources_cannot_be_tracked(self):
        bridge = GitHub()
        source = {"reference_kind": "commit", "ref": "a" * 40}
        with patch.object(bridge.client, "resolve", return_value=source):
            from skill_manager._source_records import SourceError

            with self.assertRaises(SourceError):
                bridge.resolve("https://github.com/example/resources", track=True)
