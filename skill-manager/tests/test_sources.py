"""Skill-owned acquisition/jobs qualification, independent of resource-sources."""

import base64
import tempfile
import time
import unittest
import uuid
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from skill_manager import Manager
from skill_manager._source_records import SourceError, digest
from skill_manager.source_jobs import run, start, status


def tree(files):
    return {
        "schema": "resource-sources/tree-1",
        "directories": [],
        "files": {
            path: {
                "content_b64": base64.b64encode(
                    value if isinstance(value, bytes) else value.encode()
                ).decode(),
                "executable": path.endswith(".py"),
            }
            for path, value in files.items()
        },
    }


class GitHubFixture:
    def __init__(self, files):
        self.tree = tree(files)
        self.head = "a" * 40
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
            "commit": self.head,
            "pinned": track is False,
        }

    def snapshot(self, source):
        return deepcopy(self.tree), digest(self.tree)

    def relation(self, source, commit):
        return "identical" if source["commit"] == commit else self.relation_status

    def change(self, files):
        self.tree = tree(files)
        self.head = chr(ord(self.head[0]) + 1) * 40


class SourcesTests(unittest.TestCase):
    def manager(self, kind, files):
        self.assertEqual("skills", kind)
        result = Manager(self.root / kind)
        result.source_client = GitHubFixture(files)
        return result

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def skill(self, content="Original", **extra):
        return {
            "skills/review/SKILL.md": "---\nname: review\ndescription: Review code\n---\n\n"
            + content,
            **extra,
        }

    def imported(self, m):
        discovery = m.discover_source("https://github.com/example/resources")
        return m.import_source(discovery["discovery_id"])["items"][0]

    def test_complete_skill_and_duplicate_import_after_edit(self):
        m = self.manager(
            "skills",
            self.skill(
                **{
                    "skills/review/scripts/check.py": b"print(1)",
                    "skills/review/assets/data.bin": b"\x00\xff",
                }
            ),
        )
        initial = self.imported(m)
        pub, item = initial["publisher_id"], initial["id"]
        self.assertEqual(m.read_managed_file(pub, item, "assets/data.bin"), b"\x00\xff")
        saved = m.save_managed(
            pub,
            item,
            {
                "SKILL.md": {
                    "content": self.skill("Mine")["skills/review/SKILL.md"].encode(),
                    "executable": False,
                }
            },
            expected_revision=initial["revision"],
        )
        duplicate = self.imported(m)
        self.assertEqual(duplicate["revision"], saved["revision"])
        m.source_client.change(self.skill("Publisher"))
        pending = m.check_updates(pub, item, force=True)
        self.assertTrue(pending["reconciliation_required"])
        self.assertIn(b"Mine", m.read_managed_file(pub, item, "SKILL.md"))
        kept = m.reconcile(
            pub, item, expected_revision=pending["revision"], take="managed"
        )
        self.assertFalse(kept["reconciliation_required"])
        self.assertTrue(kept["customized"])
        self.assertEqual(
            m.check_updates(pub, item, force=True)["revision"], kept["revision"]
        )

    def test_uncustomized_update_ignores_unrelated_files_and_rollback_holds(self):
        m = self.manager("skills", self.skill())
        first = self.imported(m)
        pub, item = first["publisher_id"], first["id"]
        m.source_client.change(self.skill(**{"README.md": "Unrelated"}))
        self.assertEqual(
            m.check_updates(pub, item, force=True)["revision"], first["revision"]
        )
        m.source_client.change(self.skill("New"))
        updated = m.check_updates(pub, item, force=True)
        self.assertEqual(updated["outcome"], "updated")
        rolled = m.rollback_managed(
            pub, item, first["revision"], expected_revision=updated["revision"]
        )
        m.source_client.change(self.skill("Next"))
        pending = m.check_updates(pub, item, force=True)
        self.assertTrue(pending["reconciliation_required"])
        self.assertEqual(pending["active_digest"], rolled["active_digest"])

    def test_reimport_refreshes_an_existing_source_and_preserves_pins(self):
        m = self.manager("skills", self.skill())
        first = self.imported(m)
        m.source_client.change(self.skill("New publisher"))
        refreshed = self.imported(m)
        self.assertEqual(refreshed["outcome"], "updated")
        pinned = m.source_settings(
            first["publisher_id"],
            first["id"],
            expected_revision=refreshed["revision"],
            pinned=True,
        )
        m.source_client.change(self.skill("Another publisher"))
        repeated = self.imported(m)
        self.assertEqual(repeated["outcome"], "pinned")
        self.assertEqual(repeated["revision"], pinned["revision"])

    def test_diverged_source_requires_review_even_when_untouched(self):
        m = self.manager("skills", self.skill())
        first = self.imported(m)
        m.source_client.change(self.skill("Rewritten"))
        m.source_client.relation_status = "diverged"
        changed = m.check_updates(first["publisher_id"], first["id"], force=True)
        self.assertTrue(changed["source_review"])
        self.assertEqual(changed["active_digest"], first["active_digest"])
        accepted = m.reconcile(
            first["publisher_id"],
            first["id"],
            expected_revision=changed["revision"],
            take="publisher",
        )
        self.assertFalse(accepted["reconciliation_required"])

    def test_failure_preserves_active_and_exposes_diagnostic(self):
        m = self.manager("skills", self.skill())
        first = self.imported(m)
        m.source_client.change({"README.md": "Removed"})
        with self.assertRaises(Exception):
            m.check_updates(first["publisher_id"], first["id"], force=True)
        current = m.managed_status(first["publisher_id"], first["id"])
        self.assertEqual(current["active_digest"], first["active_digest"])
        self.assertIsNotNone(current["sync_error"])

    def test_pinning_and_tracking_pause_do_not_fetch(self):
        m = self.manager("skills", self.skill())
        first = self.imported(m)
        pinned = m.source_settings(
            first["publisher_id"],
            first["id"],
            expected_revision=first["revision"],
            pinned=True,
        )
        with patch.object(
            m.source_client, "resolve", side_effect=AssertionError("Unexpected fetch")
        ):
            self.assertEqual(
                m.check_updates(first["publisher_id"], first["id"], force=True)[
                    "outcome"
                ],
                "pinned",
            )
        self.assertTrue(pinned["source"]["pinned"])

    def test_local_copy_survives_publisher_changes_and_removal(self):
        m = self.manager("skills", {})
        source = self.root / "local" / "review"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(self.skill()["skills/review/SKILL.md"])
        imported = m.import_local(source)
        pub, item = imported["publisher_id"], imported["id"]
        edited = m.save_managed(
            pub,
            item,
            {
                "SKILL.md": {
                    "content": self.skill("My local copy")[
                        "skills/review/SKILL.md"
                    ].encode(),
                    "executable": False,
                }
            },
            expected_revision=imported["revision"],
        )
        (source / "SKILL.md").write_text(
            self.skill("Publisher changed")["skills/review/SKILL.md"]
        )
        self.assertEqual(m.import_local(source)["revision"], edited["revision"])
        import shutil

        shutil.rmtree(source)
        self.assertIn(b"My local copy", m.read_managed_file(pub, item, "SKILL.md"))

    def test_source_fetch_cannot_overwrite_concurrent_edit(self):
        m = self.manager("skills", self.skill())
        first = self.imported(m)
        pub, item = first["publisher_id"], first["id"]
        original = m.source_client.snapshot
        m.source_client.change(self.skill("Publisher"))

        def snapshot(source):
            m.save_managed(
                pub,
                item,
                {
                    "SKILL.md": {
                        "content": self.skill("Mine")[
                            "skills/review/SKILL.md"
                        ].encode(),
                        "executable": False,
                    }
                },
                expected_revision=first["revision"],
            )
            return original(source)

        m.source_client.snapshot = snapshot
        with self.assertRaises(Exception) as error:
            m.check_updates(pub, item, force=True)
        self.assertEqual(error.exception.code, "conflict")
        self.assertIn(b"Mine", m.read_managed_file(pub, item, "SKILL.md"))

    def test_multi_skill_selection_validates_before_any_publish(self):
        m = self.manager("skills", self.skill(**{"bad/SKILL.md": "Invalid"}))
        discovery = m.discover_source("https://github.com/example/resources")
        with self.assertRaises(Exception):
            m.import_source(
                discovery["discovery_id"],
                [row["id"] for row in discovery["candidates"]],
            )
        self.assertEqual(m.managed_catalog(), [])

    def test_switching_reference_stages_review_and_settings_are_guarded(self):
        m = self.manager("skills", self.skill())
        first = self.imported(m)
        m.source_client.change(self.skill("Different branch"))
        changed = m.switch_source(
            first["publisher_id"],
            first["id"],
            expected_revision=first["revision"],
            ref="other",
            track=True,
        )
        self.assertTrue(changed["reconciliation_required"])
        self.assertEqual(changed["active_digest"], first["active_digest"])
        with self.assertRaises(Exception):
            m.source_settings(
                first["publisher_id"],
                first["id"],
                expected_revision=first["revision"],
                label="Stale",
            )

    def test_cancel_does_not_signal_reused_worker_identity(self):
        import os
        from types import SimpleNamespace

        from skill_manager._source_records import atomic, read

        m = self.manager("skills", {})
        with patch(
            "skill_manager.source_jobs.subprocess.Popen",
            return_value=SimpleNamespace(pid=os.getpid(), wait=lambda: 0),
        ):
            job = start(m, "create", {"name": "example", "description": "Example"})
        path = m.home / "source-jobs" / job["id"] / "status.json"
        atomic(path, read(path) | {"process_identity": "different-process"})
        with patch("skill_manager.source_jobs.os.killpg") as kill:
            cancelled = m.cancel_source_job(job["id"])
        self.assertEqual(cancelled["status"], "interrupted")
        kill.assert_not_called()

    def test_retained_create_job_runs_without_suite_and_retries_once(self):
        m = self.manager("skills", {})
        request = {
            "name": "example",
            "content": "Instructions",
            "description": "Example",
        }
        key = str(uuid.uuid4())
        row = start(m, "create", request, key=key)
        deadline = time.monotonic() + 15
        while row["status"] in ("queued", "running") and time.monotonic() < deadline:
            time.sleep(0.05)
            row = status(m, key)
        self.assertEqual(row["status"], "completed", row)
        self.assertEqual(start(m, "create", request, key=key), row)
        self.assertEqual(len(m.managed_catalog()), 1)
        with self.assertRaises(SourceError):
            start(m, "create", request | {"content": "Different"}, key=key)

    def test_retained_check_job_uses_public_identity_and_keeps_custom_content(self):
        import os
        from types import SimpleNamespace

        m = self.manager("skills", self.skill())
        first = self.imported(m)
        m.source_client.change(self.skill("Next"))
        with patch(
            "skill_manager.source_jobs.subprocess.Popen",
            return_value=SimpleNamespace(pid=os.getpid(), wait=lambda: 0),
        ):
            row = start(
                m,
                "check",
                {
                    "publisher": first["publisher_id"],
                    "identifier": first["id"],
                    "force": True,
                },
            )
        run(m, row["id"])
        completed = status(m, row["id"])
        self.assertEqual(completed["status"], "completed", completed)
        self.assertEqual(completed["result"]["outcome"], "updated")
