"""Behavioral contract for owned skill registrations and reconciliation."""

import json
import shutil
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch


class LifecycleTests(unittest.TestCase):
    publisher = "tool"
    identifier = "main"

    def status(self):
        return self.manager.managed_status(self.publisher, self.identifier)

    def save(self, text, *, file="body.md", revision=None):
        return self.manager.save_managed(
            self.publisher,
            self.identifier,
            self.file_changes({file: text}),
            expected_revision=revision or self.status()["revision"],
        )

    def pending(self):
        self.ensure(1)
        self.save("user\nmiddle\nlast\n")
        self.write_source("body.md", "first\nmiddle\npublisher\n")
        return self.ensure(2)

    def test_registration_is_owned_idempotent_and_survives_source_removal(self):
        self.assertEqual([], self.manager.managed_catalog())
        self.assertFalse(self.home.exists())
        first = self.ensure(1)
        self.assertEqual("imported", first["outcome"])
        second = self.ensure(1)
        self.assertEqual(first["revision"], second["revision"])
        self.assertEqual(1, len(self.manager.managed_history(self.publisher, self.identifier)))
        shutil.rmtree(self.source)
        self.assertEqual("first\nmiddle\nlast\n", self.content())
        self.assertFalse(self.status()["reconciliation_required"])

    def test_publisher_updates_uncustomized_copy_and_old_revisions_remain_pinned(self):
        initial = self.ensure(1)
        self.write_source("body.md", "updated\n")
        updated = self.ensure(2)
        self.assertEqual("updated", updated["outcome"])
        self.assertEqual("updated\n", self.content())
        self.assertEqual("first\nmiddle\nlast\n", self.content(revision=initial["revision"]))
        self.assertFalse(updated["customized"])
        self.assertEqual("ignored_older", self.ensure(1)["outcome"])
        self.assertEqual(updated["revision"], self.status()["revision"])

    def test_unordered_content_changes_require_review(self):
        self.ensure(None)
        self.write_source("body.md", "unordered change\n")
        pending = self.ensure(None)
        self.assertTrue(pending["reconciliation_required"])
        self.assertEqual("first\nmiddle\nlast\n", self.content())

    def test_customized_copy_stays_active_until_explicit_clean_merge(self):
        pending = self.pending()
        self.assertEqual("user\nmiddle\nlast\n", self.content())
        self.assertTrue(pending["customized"])
        self.assertTrue(pending["reconciliation_required"])
        preview = self.manager.preview_reconciliation(self.publisher, self.identifier)
        self.assertFalse(preview["conflicts"])
        self.assertTrue(preview["valid"])
        self.assertTrue(preview["managed_changes"])
        self.assertTrue(preview["publisher_changes"])
        self.assertEqual(pending["revision"], self.ensure(2)["revision"])
        applied = self.manager.reconcile(
            self.publisher, self.identifier, expected_revision=preview["revision"]
        )
        self.assertEqual("user\nmiddle\npublisher\n", self.content())
        self.assertFalse(applied["reconciliation_required"])
        self.assertTrue(applied["customized"])
        self.assertEqual(applied["revision"], self.ensure(2)["revision"])

    def test_conflict_requires_a_choice_and_keep_mine_advances_baseline(self):
        self.ensure(1)
        self.save("user\n")
        self.write_source("body.md", "publisher\n")
        self.ensure(2)
        preview = self.manager.preview_reconciliation(self.publisher, self.identifier)
        self.assertEqual(1, len(preview["conflicts"]))
        with self.assertRaises(self.error):
            self.manager.reconcile(
                self.publisher, self.identifier, expected_revision=preview["revision"]
            )
        conflict = preview["conflicts"][0]["path"]
        accepted = self.manager.reconcile(
            self.publisher,
            self.identifier,
            expected_revision=preview["revision"],
            resolutions={conflict: {"take": "managed"}},
        )
        self.assertEqual("user\n", self.content())
        self.assertFalse(accepted["reconciliation_required"])
        self.assertEqual(accepted["upstream"], accepted["baseline"])
        self.assertEqual(accepted["revision"], self.ensure(2)["revision"])
        self.write_source("notes.md", "next publisher addition\n")
        self.assertTrue(self.ensure(3)["reconciliation_required"])
        self.assertEqual("user\n", self.content())

    def test_accept_publisher_and_manual_conflict_resolution(self):
        self.ensure(1)
        self.save("user\n")
        self.write_source("body.md", "publisher\n")
        self.ensure(2)
        preview = self.manager.preview_reconciliation(self.publisher, self.identifier)
        path = preview["conflicts"][0]["path"]
        self.manager.reconcile(
            self.publisher,
            self.identifier,
            expected_revision=preview["revision"],
            resolutions={path: {"value": "combined\n"}},
        )
        self.assertEqual("combined\n", self.content())
        self.write_source("body.md", "third\n")
        pending = self.ensure(3)
        accepted = self.manager.reconcile(
            self.publisher, self.identifier, expected_revision=pending["revision"], take="publisher"
        )
        self.assertEqual("third\n", self.content())
        self.assertFalse(accepted["customized"])

    def test_stale_editor_and_reconciliation_guards(self):
        pending = self.pending()
        self.save("second user edit\n")
        with self.assertRaises(self.error):
            self.save("stale edit\n", revision=pending["revision"])
        with self.assertRaises(self.error):
            self.manager.reconcile(
                self.publisher,
                self.identifier,
                expected_revision=pending["revision"],
                take="publisher",
            )
        self.assertEqual("second user edit\n", self.content())

    def test_new_pending_update_invalidates_old_review(self):
        pending = self.pending()
        self.write_source("notes.md", "newest\n")
        latest = self.ensure(3)
        self.assertNotEqual(pending["revision"], latest["revision"])
        with self.assertRaises(self.error):
            self.manager.reconcile(
                self.publisher,
                self.identifier,
                expected_revision=pending["revision"],
                take="publisher",
            )
        self.assertEqual("user\nmiddle\nlast\n", self.content())

    def test_invalid_update_records_error_and_preserves_active_copy(self):
        initial = self.ensure(1)
        self.invalidate_source()
        with self.assertRaises(self.error):
            self.ensure(2)
        current = self.status()
        self.assertEqual(initial["active_digest"], current["active_digest"])
        self.assertIsNotNone(current["sync_error"])
        self.assertEqual("first\nmiddle\nlast\n", self.content())

    def test_reused_publisher_revision_is_rejected_and_error_can_clear(self):
        self.ensure(1)
        self.write_source("body.md", "changed without new revision\n")
        with self.assertRaises(self.error):
            self.ensure(1)
        self.assertIsNotNone(self.status()["sync_error"])
        self.write_source("body.md", "first\nmiddle\nlast\n")
        cleared = self.ensure(1)
        self.assertIsNone(cleared["sync_error"])

    def test_unversioned_caller_cannot_replace_ordered_stream(self):
        initial = self.ensure(1)
        self.write_source("body.md", "unordered\n")
        with self.assertRaises(self.error):
            self.ensure(None)
        self.assertEqual(initial["active_digest"], self.status()["active_digest"])

    def test_concurrent_ensure_creates_one_revision(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            rows = list(executor.map(lambda _: self.ensure(1), range(8)))
        self.assertEqual(1, len({row["revision"] for row in rows}))
        self.assertEqual(1, len(self.manager.managed_history(self.publisher, self.identifier)))

    def test_publisher_paths_can_move_without_losing_identity_or_edits(self):
        self.ensure(1)
        edited = self.save("user\n")
        moved = self.root / "relocated" / self.source.name
        moved.parent.mkdir()
        shutil.move(self.source, moved)
        self.source = moved
        self.assertEqual(edited["revision"], self.ensure(1)["revision"])
        self.assertEqual("user\n", self.content())

    def test_rollback_retains_new_publisher_baseline(self):
        initial = self.ensure(1)
        self.write_source("body.md", "publisher v2\n")
        updated = self.ensure(2)
        rollback = self.manager.rollback_managed(
            self.publisher,
            self.identifier,
            initial["revision"],
            expected_revision=updated["revision"],
        )
        self.assertEqual("first\nmiddle\nlast\n", self.content())
        self.assertEqual(updated["baseline"], rollback["baseline"])
        self.assertTrue(rollback["customized"])
        self.assertEqual(rollback["revision"], self.ensure(2)["revision"])

    def test_unknown_conflict_paths_are_rejected(self):
        pending = self.pending()
        with self.assertRaises(self.error):
            self.manager.reconcile(
                self.publisher,
                self.identifier,
                expected_revision=pending["revision"],
                resolutions={"/not-a-conflict": {"take": "publisher"}},
            )

    def test_restoring_baseline_content_clears_customized_state(self):
        self.ensure(1)
        self.save("user\n")
        restored = self.save("first\nmiddle\nlast\n")
        self.assertFalse(restored["customized"])

    def test_invalid_identifiers_cannot_escape_store(self):
        for publisher, identifier in (("../outside", "main"), ("tool", "../outside")):
            with self.assertRaises(self.error):
                self.manager.managed_status(publisher, identifier)

    def test_stopping_tracking_survives_automatic_registration(self):
        initial = self.ensure(1)
        stopped = self.manager.set_managed_tracking(
            self.publisher, self.identifier, False, expected_revision=initial["revision"]
        )
        self.write_source("body.md", "publisher v2\n")
        self.assertEqual("tracking_stopped", self.ensure(2)["outcome"])
        self.assertEqual(stopped["revision"], self.status()["revision"])
        self.assertEqual("first\nmiddle\nlast\n", self.content())
        self.manager.set_managed_tracking(
            self.publisher, self.identifier, True, expected_revision=stopped["revision"]
        )
        self.ensure(2)
        self.assertEqual("publisher v2\n", self.content())

    def test_same_pending_hash_with_new_version_does_not_apply_it(self):
        pending = self.pending()
        newer = self.ensure(3)
        self.assertEqual(pending["active_digest"], newer["active_digest"])
        self.assertTrue(newer["reconciliation_required"])

    def test_accepting_keep_mine_never_requires_the_same_update_again(self):
        pending = self.pending()
        applied = self.manager.reconcile(
            self.publisher, self.identifier, expected_revision=pending["revision"], take="managed"
        )
        self.assertFalse(applied["reconciliation_required"])
        self.assertEqual("user\nmiddle\nlast\n", self.content())
        self.assertEqual(applied["revision"], self.ensure(2)["revision"])

    def test_pointer_write_failure_preserves_active_content_and_history(self):
        initial = self.ensure(1)
        backend = self.manager._managed().backend
        write = type(backend).write

        def fail_pointer(instance, path, row, *, immutable=False):
            if path.parent.parent.name == "managed":
                raise OSError("simulated pointer write failure")
            return write(instance, path, row, immutable=immutable)

        with patch.object(type(backend), "write", fail_pointer):
            with self.assertRaises(OSError):
                self.save("not committed\n")
        self.assertEqual(initial["revision"], self.status()["revision"])
        self.assertEqual(1, len(self.manager.managed_history(self.publisher, self.identifier)))
        self.assertEqual("first\nmiddle\nlast\n", self.content())

    def test_missing_active_object_fails_resolution_and_isolated_catalog(self):
        initial = self.ensure(1)
        path = self.home / "objects" / (initial["active_digest"][7:] + ".json")
        path.unlink()
        with self.assertRaises(self.error):
            self.content()
        self.assertIn("error", self.manager.managed_catalog()[0])

    def test_legacy_adoption_preserves_content_and_requires_two_way_review(self):
        publisher = self.root / "publisher" / self.source.name
        publisher.parent.mkdir()
        shutil.copytree(self.source, publisher)
        (publisher / "body.md").write_text("new publisher\n")
        row = self.manager.adopt_legacy(
            self.publisher, self.identifier, self.source, publisher, publisher_revision=2
        )
        self.assertFalse(row["baseline_known"])
        self.assertTrue(row["customized"])
        self.assertTrue(row["reconciliation_required"])
        self.assertEqual("first\nmiddle\nlast\n", self.content())
        preview = self.manager.preview_reconciliation(self.publisher, self.identifier)
        self.assertTrue(preview["conflicts"])
        self.assertFalse(preview["baseline_known"])
        self.manager.ensure_registered(
            self.publisher,
            publisher,
            **(
                {"package_id": self.identifier}
                if self.manager.__class__.__module__.startswith("prompt")
                else {"skill_id": self.identifier}
            ),
            publisher_revision=3,
        )
        self.assertEqual("first\nmiddle\nlast\n", self.content())
        current = self.status()
        reconciled = self.manager.reconcile(
            self.publisher, self.identifier, expected_revision=current["revision"], take="managed"
        )
        self.assertTrue(reconciled["baseline_known"])
        self.assertFalse(reconciled["reconciliation_required"])

    def test_lightweight_summaries_do_not_read_blobs_and_file_views_are_bounded(self):
        self.ensure(1)
        view = self.manager.managed_view(self.publisher, self.identifier)
        pointer = next(row["path"] for row in view["files"] if row["label"] == "body.md")
        file_view = self.manager.managed_view(
            self.publisher, self.identifier, path=pointer, limit=4
        )
        self.assertEqual("firs", file_view["sides"]["managed"]["text"])
        self.assertEqual(4, file_view["sides"]["managed"]["next_offset"])
        backend = self.manager._managed().backend
        with patch.object(
            type(backend), "snapshot", side_effect=AssertionError("summary read content")
        ):
            self.assertEqual(
                self.status()["revision"], self.manager.managed_summaries()[0]["revision"]
            )

    def test_review_summary_omits_content_and_rejects_stale_views(self):
        row = self.pending()
        view = self.manager.managed_view(self.publisher, self.identifier, review=True)
        self.assertNotIn("candidate", view)
        self.assertTrue(view["files"])
        self.save("another edit\n")
        with self.assertRaises(self.error):
            self.manager.managed_view(
                self.publisher, self.identifier, review=True, expected_revision=row["revision"]
            )

    def test_unicode_preview_chunks_bound_escaped_bytes_and_make_progress(self):
        self.ensure(1)
        text = "😀世界\n" * 30
        self.save(text)
        offset, chunks = 0, []
        while offset is not None:
            view = self.manager.managed_view(
                self.publisher, self.identifier, path="/files/body.md", offset=offset, limit=37
            )
            side = view["sides"]["managed"]
            self.assertLessEqual(len(json.dumps(side["text"])) - 2, 37)
            self.assertTrue(side["text"])
            chunks.append(side["text"])
            offset = side["next_offset"]
        self.assertEqual(text, "".join(chunks))
