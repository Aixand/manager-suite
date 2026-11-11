"""Retained source/store compatibility and MCP-owned source update policy."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from github_source import GitHubError
from mcp_manager import Manager, McpError
from mcp_manager.installations.service import runtime
from mcp_manager.jobs import sources as source_jobs
from mcp_manager.sources.github import GitHub
from mcp_manager.sources.records import atomic, digest
from source_fixture import GitHubFixture

FIXTURE = Path(__file__).parent / "fixtures/pre_sources_migration.json"


class SourceMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = json.loads(FIXTURE.read_text())
        for name, content in self.fixture["records"].items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self.manager = Manager(self.root)
        self.publisher = self.fixture["publisher"]
        self.identifier = self.fixture["identifier"]

    def test_legacy_revisions_objects_discovery_and_jobs_read_without_rewriting(self):
        before = {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*.json")
        }
        resolved = self.manager.resolve_managed(self.publisher, self.identifier)
        self.assertEqual(
            resolved["resolution"]["revision"], self.fixture["edited_revision"]
        )
        self.assertEqual(resolved["definition"]["args"], ["--custom"])
        historical = self.manager.resolve_managed(
            self.publisher, self.identifier, revision=self.fixture["initial_revision"]
        )
        self.assertEqual(historical["definition"]["args"], [])
        repository = self.manager._source_service.backend()
        discovery = repository.discovery(self.fixture["discovery_id"])
        self.assertEqual(
            discovery["source"]["source_digest"], self.fixture["source_digest"]
        )
        tree = repository.tree(self.fixture["source_digest"])
        self.assertEqual(tree["directories"], ["z-empty", "a-empty"])
        self.assertEqual(digest(tree), self.fixture["source_digest"])
        with patch.object(
            source_jobs.subprocess,
            "Popen",
            side_effect=AssertionError("must not replay"),
        ):
            row = self.manager.start_source_job(
                "create", self.fixture["job_request"], key=self.fixture["job_id"]
            )
        self.assertEqual(row["result"], {"legacy": True})
        self.assertEqual(row["schema"], "resource-sources/job-1")
        after = {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*.json")
        }
        self.assertEqual(after, before)

    def test_legacy_runtime_index_resolves_original_generation_and_detects_tampering(
        self,
    ):
        row = json.loads(
            json.dumps(self.fixture["runtime_template"]).replace(
                "@HOME@", str(self.root)
            )
        )
        entry = Path(row["entrypoint"])
        entry.parent.mkdir(parents=True)
        entry.write_bytes(b"legacy executable")
        row["command"] = sys.executable
        # Independent historical compact encoder, with the relocated fixture home.
        row["digest"] = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    {key: value for key, value in row.items() if key != "digest"},
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
        )
        path = self.root / "runtime-index" / (row["recipe_digest"][7:] + ".json")
        atomic(path, row)
        definition = self.manager.resolve_managed(self.publisher, self.identifier)[
            "definition"
        ]
        self.assertEqual(
            runtime(self.root, definition["installation"])["generation"],
            row["generation"],
        )
        inspected = self.manager.inspect_managed(
            self.publisher, self.identifier, self.root
        )
        self.assertEqual(inspected["states"][0]["generation"], row["generation"])
        self.assertEqual(
            next(iter(inspected["servers"].values()))["args"], ["--custom"]
        )
        entry.write_bytes(b"tampered")
        with self.assertRaises(McpError) as caught:
            runtime(self.root, definition["installation"])
        self.assertEqual(caught.exception.code, "integrity")


class GitHubPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manager = Manager(self.root / "store")
        self.client = GitHubFixture(self.files("first"))
        self.manager.source_client = self.client

    @staticmethod
    def files(value):
        return {"server.json": json.dumps({"command": sys.executable, "args": [value]})}

    def imported(self):
        discovery = self.manager.discover_source("https://github.com/example/resources")
        return self.manager.import_source(discovery["discovery_id"])["items"][0]

    def test_forward_updates_customization_and_rollback_hold(self):
        first = self.imported()
        publisher, identifier = first["publisher_id"], first["id"]
        self.client.change(self.files("second"))
        second = self.manager.check_updates(publisher, identifier, force=True)
        self.assertEqual(second["outcome"], "updated")
        self.assertEqual(
            self.manager.resolve_managed(publisher, identifier)["definition"]["args"],
            ["second"],
        )
        self.manager.save_managed(
            publisher,
            identifier,
            {"command": sys.executable, "args": ["custom"]},
            expected_revision=second["revision"],
        )
        self.client.change(self.files("third"))
        pending = self.manager.check_updates(publisher, identifier, force=True)
        self.assertEqual(pending["outcome"], "update_pending")
        self.assertEqual(
            self.manager.resolve_managed(publisher, identifier)["definition"]["args"],
            ["custom"],
        )
        restored = self.manager.rollback_managed(
            publisher,
            identifier,
            first["revision"],
            expected_revision=pending["revision"],
        )
        self.client.change(self.files("fourth"))
        held = self.manager.check_updates(publisher, identifier, force=True)
        self.assertEqual(held["outcome"], "update_pending")
        self.assertEqual(held["active_digest"], restored["active_digest"])
        self.assertTrue(self.client.comparisons)

    def test_pinning_fetch_failure_and_source_switch_preserve_accepted_copy(self):
        item = self.imported()
        publisher, identifier = item["publisher_id"], item["id"]
        pinned = self.manager.source_settings(
            publisher, identifier, pinned=True, expected_revision=item["revision"]
        )
        fetch_count = len(self.client.fetches)
        self.assertEqual(
            self.manager.check_updates(publisher, identifier, force=True)["outcome"],
            "pinned",
        )
        self.assertEqual(len(self.client.fetches), fetch_count)
        tracked = self.manager.source_settings(
            publisher, identifier, pinned=False, expected_revision=pinned["revision"]
        )
        self.client.change(self.files("next"))
        with patch.object(
            self.client, "fetch", side_effect=GitHubError("network", "fixture failure")
        ):
            with self.assertRaises(McpError) as caught:
                self.manager.check_updates(publisher, identifier, force=True)
        self.assertEqual(caught.exception.code, "network")
        failed = self.manager.managed_status(publisher, identifier)
        self.assertEqual(failed["active_digest"], tracked["active_digest"])
        self.assertEqual(failed["sync_error"]["code"], "network")
        switched = self.manager.switch_source(
            publisher, identifier, ref="v1", expected_revision=failed["revision"]
        )
        self.assertEqual(switched["outcome"], "update_pending")
        self.assertTrue(switched["source"]["pinned"])
        self.assertEqual(switched["active_digest"], item["active_digest"])

    def test_package_file_fetches_siblings_and_explicit_commit_cannot_track(self):
        adapter = GitHub(self.client)
        source = adapter.resolve(
            "https://github.com/example/resources", path="servers/docs/package.json"
        )
        adapter.snapshot(source)
        self.assertEqual(self.client.fetches[-1]["path"], "servers/docs")
        self.assertEqual(source["path"], "servers/docs/package.json")
        self.assertTrue(
            adapter.resolve("https://github.com/example/resources", ref="v1")["pinned"]
        )
        with self.assertRaises(McpError):
            adapter.resolve(
                "https://github.com/example/resources", ref="a" * 40, track=True
            )

    def test_queued_legacy_job_cancellation_prevents_publication(self):
        identifier = "00000000-0000-0000-0000-000000000003"
        path = self.manager.home / "source-jobs" / identifier
        atomic(
            path / "request.json",
            {
                "operation": "create",
                "request": {
                    "name": "Cancelled",
                    "definition": {"command": sys.executable},
                },
                "capture": None,
            },
        )
        atomic(
            path / "status.json",
            {
                "schema": "resource-sources/job-1",
                "id": identifier,
                "status": "cancelled",
                "operation": "create",
                "pid": None,
            },
        )
        source_jobs.run(self.manager._source_service.backend(), identifier)
        self.assertEqual(self.manager.source_job(identifier)["status"], "cancelled")
        self.assertEqual(self.manager.managed_catalog(), [])

    def test_source_cli_uses_native_retained_worker_and_reports_errors(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "mcp_manager",
                "--home",
                str(self.manager.home),
                "managed",
                "create",
                "Docs",
                "--definition",
                json.dumps({"command": sys.executable}),
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        row = json.loads(result.stdout)
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["result"]["publisher_id"], "local")
        self.assertTrue(self.manager.managed_catalog())
