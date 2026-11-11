import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from prompt_manager import Manager, PromptError
from prompt_manager.storage import files, locks, objects


class WriteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "state.json"
        files.atomic_json(self.path, {"value": "old"})

    def test_mutable_write_synchronizes_parent_after_replacement(self):
        events = []
        replace = os.replace
        sync = files.sync_directory

        def replaced(source, destination):
            replace(source, destination)
            events.append("replace")

        def synchronized(directory):
            sync(directory)
            events.append("directory_sync")

        with (
            patch.object(files.os, "replace", side_effect=replaced),
            patch.object(files, "sync_directory", side_effect=synchronized),
        ):
            files.atomic_json(self.path, {"value": "new"})
        self.assertEqual(["replace", "directory_sync"], events)
        self.assertEqual({"value": "new"}, files.read_json(self.path))

    def test_failures_before_replacement_preserve_old_complete_record(self):
        for target in ("fsync", "replace"):
            with (
                self.subTest(stage=target),
                patch.object(files.os, target, side_effect=OSError("Interrupted")),
            ):
                with self.assertRaises(OSError):
                    files.atomic_json(self.path, {"value": "new"})
            self.assertEqual({"value": "old"}, files.read_json(self.path))
            self.assertEqual([], list(self.root.glob(".registry-*")))

    def test_failure_after_replacement_is_visible_and_equal_retry_resyncs(self):
        with patch.object(files, "sync_directory", side_effect=OSError("Directory sync failed")):
            with self.assertRaises(OSError):
                files.atomic_json(self.path, {"value": "new"})
        self.assertEqual({"value": "new"}, files.read_json(self.path))
        with patch.object(files, "sync_directory", wraps=files.sync_directory) as sync:
            files.atomic_json(self.path, {"value": "new"})
        sync.assert_called_once_with(self.root)

    def test_new_directory_entries_are_synchronized_in_parent_order(self):
        target = self.root / "one" / "two"
        with patch.object(files, "sync_directory", wraps=files.sync_directory) as sync:
            files.ensure_directory(target)
        self.assertEqual(
            [self.root, self.root / "one"], [call.args[0] for call in sync.call_args_list]
        )

    def test_immutable_publication_cannot_replace_content_and_retries_sync(self):
        target = self.root / "immutable.json"
        with patch.object(objects.os, "link", side_effect=OSError("Interrupted")):
            with self.assertRaises(OSError):
                objects.publish_immutable(target, {"value": "first"})
        self.assertFalse(target.exists())
        self.assertEqual([], list(self.root.glob(".publish-*")))
        objects.publish_immutable(target, {"value": "first"})
        with self.assertRaises(PromptError):
            objects.publish_immutable(target, {"value": "replacement"})
        self.assertEqual({"value": "first"}, files.read_json(target))
        with patch.object(objects, "sync_directory", wraps=objects.sync_directory) as sync:
            objects.publish_immutable(target, {"value": "first"})
        sync.assert_called_once_with(self.root)

    def test_windows_lock_initialization_does_not_grow_existing_lock_file(self):
        # Exercise byte-range protocol without claiming native Windows verification.
        windows = types.SimpleNamespace(locking=Mock(), LK_LOCK=1, LK_UNLCK=2)
        target = self.root / "windows.lock"
        with target.open("a+b") as stream:
            with (
                patch.object(locks.os, "name", "nt"),
                patch.dict("sys.modules", {"msvcrt": windows}),
            ):
                for _ in range(5):
                    locks.acquire(stream)
                    locks.release(stream)
        self.assertEqual(b"0", target.read_bytes())
        self.assertEqual(10, windows.locking.call_count)


class DescriptorDurabilityTests(unittest.TestCase):
    def test_descriptor_removal_syncs_parent_and_reports_post_unlink_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = Manager(Path(directory) / "store", packs=[])
            source = manager.register_source("source", [directory], expected_revision=None)
            parent = manager.store.home / "sources"

            def after_unlink(path):
                self.assertEqual(parent, path)
                self.assertFalse((path / "source.json").exists())

            with patch(
                "prompt_manager.sources.registry.sync_directory", side_effect=after_unlink
            ) as sync:
                manager.unregister_source("source", expected_revision=source["revision"])
            sync.assert_called_once_with(parent)
            app = manager.register_application("workspace", prompts={})
            with patch(
                "prompt_manager.sources.registry.sync_directory",
                side_effect=OSError("Directory sync failed"),
            ):
                with self.assertRaises(PromptError) as error:
                    manager.unregister_application("workspace", expected_revision=app["revision"])
            self.assertEqual("io", error.exception.code)
            with self.assertRaises(PromptError) as missing:
                manager.application_descriptor("workspace")
            self.assertEqual("not_found", missing.exception.code)
