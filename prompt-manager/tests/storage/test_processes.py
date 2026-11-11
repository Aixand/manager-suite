"""Actual process exits and contention; no required external runtime."""

import json
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from prompt_manager import Manager, PromptError

CRASH = r"""
import os
import sys
from unittest.mock import patch
from prompt_manager import Manager
from prompt_manager.managed.registration import Backend
from prompt_manager.storage import files
manager = Manager(sys.argv[1], packs=[])
phase, revision = sys.argv[2:]
store, write = Backend.store, Backend.write

def interrupted_store(backend, snapshot):
    store(backend, snapshot)
    os._exit(71)

def interrupted_write(backend, path, row, *, immutable=False):
    write(backend, path, row, immutable=immutable)
    if (phase == "after_history" and immutable) or (phase == "after_pointer" and not immutable):
        os._exit(71)

def before_replace(*args):
    os._exit(71)

with patch.object(Backend, "store", interrupted_store if phase == "after_object" else store), \
     patch.object(Backend, "write", interrupted_write), \
     patch.object(files.os, "replace", before_replace if phase == "before_replace" else os.replace):
    manager.save_managed("local", "review", {"body.md": "New content."}, expected_revision=revision)
raise AssertionError("Crash phase was not reached")
"""

MUTATE = r"""
import json
import sys
from prompt_manager import Manager, PromptError
manager = Manager(sys.argv[1], packs=[])
operation, revision, value = sys.argv[2:]
sys.stdin.readline()
if operation == "lock_edit":
    print("attempting", flush=True)
try:
    if operation == "register":
        result = manager.ensure_registered("publisher", value, publisher_revision=1)
    elif operation in {"edit", "lock_edit"}:
        result = manager.save_managed("local", "review", {"body.md": value}, expected_revision=revision)
    elif operation == "reconcile":
        result = manager.reconcile("publisher", "default", expected_revision=revision, take=value)
    else:
        result = manager.release_managed("local", "review", "shared", revision=revision)
    print(json.dumps({"ok": True, "result": result}))
except PromptError as error:
    print(json.dumps({"ok": False, "error": error.to_dict()}))
"""

HOLD = r"""
import sys
from prompt_manager.storage.locks import lock
with lock(sys.argv[1], "managed"):
    print("locked", flush=True)
    sys.stdin.readline()
"""


class ProcessStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def manager(self, name="store"):
        return Manager(self.root / name, packs=[])

    def spawn(self, script, *args):
        process = subprocess.Popen(
            [sys.executable, "-c", script, *(str(arg) for arg in args)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        def cleanup():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)

        self.addCleanup(cleanup)
        return process

    def race(self, manager, operation, revision, values):
        children = [
            self.spawn(MUTATE, manager.store.home, operation, revision, value) for value in values
        ]
        for child in children:
            child.stdin.write("go\n")
            child.stdin.flush()
        results = []
        for child in children:
            output, error = child.communicate(timeout=20)
            self.assertEqual(0, child.returncode, error)
            results.append(json.loads(output))
        return results

    def source(self, manager):
        snapshot = manager.package("local", "review").resolve()["snapshot"]
        source = self.root / "publisher"
        source.mkdir()
        (source / "pack.json").write_text(json.dumps(snapshot["pack"]))
        for path, text in snapshot["files"].items():
            (source / path).write_text(text)
        return source

    def test_process_exits_at_publication_boundaries_leave_complete_reopenable_state(self):
        for phase in ("after_object", "after_history", "before_replace", "after_pointer"):
            with self.subTest(phase=phase):
                manager = self.manager(phase)
                initial = manager.create_managed("review", content="Original.")
                crashed = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        CRASH,
                        str(manager.store.home),
                        phase,
                        initial["revision"],
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                self.assertEqual(71, crashed.returncode, crashed.stderr)
                reopened = Manager(manager.store.home, packs=[])
                package = reopened.package("local", "review")
                current = package.status()
                expected = "New content." if phase == "after_pointer" else "Original."
                self.assertIn(expected, package.render("review")["output"])
                report = reopened.check_store()
                self.assertTrue(report["ok"], report)
                self.assertEqual(0, report["counts"]["errors"])
                if phase == "after_pointer":
                    with self.assertRaises(PromptError):
                        package.save({"body.md": "Retry."}, expected_revision=initial["revision"])
                retried = package.save(
                    {"body.md": "New content."}, expected_revision=current["revision"]
                )
                self.assertEqual(retried["revision"], package.status()["revision"])
                self.assertIn("New content.", package.render("review")["output"])
                self.assertTrue(reopened.check_store()["ok"])

    def test_independent_registration_processes_are_idempotent(self):
        manager = self.manager()
        manager.create_managed("review", content="Original.")
        source = self.source(manager)
        results = self.race(manager, "register", "unused", [source, source])
        self.assertTrue(all(row["ok"] for row in results), results)
        self.assertEqual(results[0]["result"]["revision"], results[1]["result"]["revision"])
        self.assertEqual(1, len(manager.managed_history("publisher", "default")))

    def test_independent_edit_and_reconciliation_processes_reject_stale_writers(self):
        manager = self.manager()
        initial = manager.create_managed("review", content="Original.")
        edited = self.race(manager, "edit", initial["revision"], ["First.", "Second."])
        self.assertEqual(1, sum(row["ok"] for row in edited), edited)
        self.assertEqual("conflict", next(row for row in edited if not row["ok"])["error"]["code"])
        source = self.source(manager)
        baseline = manager.ensure_registered("publisher", source, publisher_revision=1)
        manager.save_managed(
            "publisher", "default", {"body.md": "User."}, expected_revision=baseline["revision"]
        )
        (source / "body.md").write_text("Publisher update.")
        pending = manager.ensure_registered("publisher", source, publisher_revision=2)
        reconciled = self.race(manager, "reconcile", pending["revision"], ["managed", "publisher"])
        self.assertEqual(1, sum(row["ok"] for row in reconciled), reconciled)
        self.assertEqual(
            "conflict", next(row for row in reconciled if not row["ok"])["error"]["code"]
        )
        self.assertTrue(manager.check_store()["ok"])

    def test_conflicting_release_processes_cannot_replace_a_label(self):
        manager = self.manager()
        first = manager.create_managed("review", content="Original.")
        second = manager.save_managed(
            "local", "review", {"body.md": "Edited."}, expected_revision=first["revision"]
        )
        children = [
            self.spawn(MUTATE, manager.store.home, "release", revision, "unused")
            for revision in (first["revision"], second["revision"])
        ]
        for child in children:
            child.stdin.write("go\n")
            child.stdin.flush()
        results = []
        for child in children:
            output, error = child.communicate(timeout=20)
            self.assertEqual(0, child.returncode, error)
            results.append(json.loads(output))
        self.assertEqual(1, sum(row["ok"] for row in results), results)
        self.assertEqual("conflict", next(row for row in results if not row["ok"])["error"]["code"])
        self.assertTrue(manager.check_store()["ok"])

    def test_process_exit_releases_a_contended_lock(self):
        manager = self.manager()
        initial = manager.create_managed("review", content="Original.")
        holder = self.spawn(HOLD, manager.store.home)
        ready = queue.Queue()
        threading.Thread(target=lambda: ready.put(holder.stdout.readline()), daemon=True).start()
        self.assertEqual("locked\n", ready.get(timeout=10))
        writer = self.spawn(
            MUTATE, manager.store.home, "lock_edit", initial["revision"], "After exit."
        )
        writer.stdin.write("go\n")
        writer.stdin.flush()
        attempting = queue.Queue()
        threading.Thread(
            target=lambda: attempting.put(writer.stdout.readline()), daemon=True
        ).start()
        self.assertEqual("attempting\n", attempting.get(timeout=10))
        with self.assertRaises(subprocess.TimeoutExpired):
            writer.wait(timeout=0.3)
        holder.kill()
        holder.wait(timeout=10)
        output, error = writer.communicate(timeout=20)
        self.assertEqual(0, writer.returncode, error)
        self.assertTrue(json.loads(output)["ok"])
        self.assertIn("After exit.", manager.render_managed("local", "review", "review")["output"])
