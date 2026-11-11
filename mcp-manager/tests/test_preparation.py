from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from mcp_manager import Manager, McpError, digest, probe
from mcp_manager import jobs
from mcp_manager.manager import read, write


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mcp-jobs-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"; self.project.mkdir()
        self.control = self.root / "control.json"
        self.log = self.root / "calls.jsonl"
        self.fixture = Path(__file__).parent / "fixtures" / "preset.py"
        self.manager = Manager(self.root / "store")
        for tool in ("sonar", "lore"):
            self.manager.add(tool, {"kind": tool, "command": sys.executable, "args": [str(self.fixture),
                "--fixture-control", str(self.control), "--fixture-log", str(self.log)]})
        self.addCleanup(self.wait_for_workers)

    def wait_for_workers(self):
        # A completed receipt can precede process exit and its log handle closing.
        # Wait before TemporaryDirectory removes these Windows fixture files.
        deadline = time.monotonic() + 30
        for row in self.manager.jobs():
            if not row.get("pid"):
                continue
            while True:
                try:
                    jobs.check_process(row["pid"])
                except ProcessLookupError:
                    break
                self.assertLess(time.monotonic(), deadline, "MCP fixture worker did not exit")
                time.sleep(.02)

    def wait(self, row):
        deadline = time.monotonic() + (60 if os.name == "nt" else 15)
        while row["status"] in ("queued", "running") and time.monotonic() < deadline:
            time.sleep(.02)
            row = self.manager.job(row["id"])
        self.assertNotIn(row["status"], ("queued", "running"), row)
        return row

    def prepare(self, tools=("sonar",), **options):
        row = self.wait(self.manager.prepare(list(tools), self.project, **options))
        self.assertEqual(row["status"], "completed", row)
        return row

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_managed_preparation_isolated_and_timeout_edits_reuse_generation(self):
        definition = self.manager.registry()["sonar"]
        registered = self.manager.ensure_registered("sonar", definition, publisher_revision=1)
        key = str(uuid.uuid4())
        first = self.wait(self.manager.prepare_managed("sonar", "default", self.project, key=key))
        self.assertEqual(first["status"], "completed", first)
        inspected = self.manager.inspect_managed("sonar", "default", self.project)
        self.assertEqual(inspected["states"][0]["status"], "prepared")
        generation = inspected["states"][0]["generation"]
        self.assertEqual(self.manager.resolve(["sonar"], self.project)["states"][0]["status"], "needs-preparation")
        saved = self.manager.save_managed("sonar", "default", definition | {"tool_timeout_sec": 99}, expected_revision=registered["revision"])
        self.assertEqual(self.manager.inspect_managed("sonar", "default", self.project)["states"][0]["generation"], generation)
        with patch.object(jobs.subprocess, "Popen", side_effect=AssertionError("must not replay a captured job")):
            self.assertEqual(self.manager.prepare_managed("sonar", "default", self.project, key=key), first)
        self.control.write_text(json.dumps({"fail_commands": ["index"]}))
        failed = self.wait(self.manager.prepare_managed("sonar", "default", self.project, refresh=True))
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(self.manager.inspect_managed("sonar", "default", self.project)["states"][0]["generation"], generation)

    def test_managed_preparation_retry_recovers_before_resolving_new_definition(self):
        definition = self.manager.registry()["sonar"]
        self.manager.ensure_registered("fixture", definition, publisher_revision=1)
        key = str(uuid.uuid4())
        first = self.wait(self.manager.prepare_managed("fixture", "default", self.project, key=key))
        current = self.manager.managed_status("fixture", "default")
        self.manager.save_managed("fixture", "default", definition | {"kind": "stdio"}, expected_revision=current["revision"])
        self.assertEqual(self.manager.prepare_managed("fixture", "default", self.project, key=key), first)
        with self.assertRaises(McpError) as error:
            self.manager.prepare_managed("fixture", "default", self.project)
        self.assertEqual(error.exception.code, "unsupported")

    def test_sonar_commands_options_and_sqlite_backup_preserve_old_generation(self):
        first = self.prepare(settings={"resolve": True, "resources": "yaml", "model_dir": str(self.root / "model")})
        old = first["completed"]["sonar"]
        with closing(sqlite3.connect(old["database"])) as connection, connection:
            connection.execute("CREATE TABLE retained(value TEXT)")
            connection.execute("INSERT INTO retained VALUES ('old-generation')")
        before = Path(old["database"]).read_bytes()
        calls = self.commands()
        self.assertEqual(len(calls), 5)
        self.assertIn("index", calls[0]); self.assertIn("yaml", calls[0])
        self.assertIn("index-navigation", calls[1])
        self.assertIn("resolve", calls[2])
        self.assertIn("embed", calls[3]); self.assertIn("separate", calls[3])
        self.assertIn("mcp", calls[4])
        refreshed = self.prepare(refresh=True)["completed"]["sonar"]
        self.assertNotEqual(old["generation"], refreshed["generation"])
        self.assertEqual(old["settings"], refreshed["settings"])
        self.assertEqual(Path(old["database"]).read_bytes(), before)
        with closing(sqlite3.connect(refreshed["database"])) as connection, connection:
            self.assertEqual(connection.execute("SELECT value FROM retained").fetchone()[0], "old-generation")
        self.assertEqual(probe({**self.manager.registry()["sonar"], "args": old["args"]}, "sonar_status")["root"], str(self.project))

    def test_lore_model_install_is_explicit_and_usage_configuration_is_retained(self):
        first = self.prepare(("lore",))["completed"]["lore"]
        self.assertFalse(any("install" in args for args in self.commands()))
        self.assertEqual(first["settings"]["model_dir"], str(self.manager.root / "models" / "lore"))
        model = str(self.root / "model"); usage = str(self.root / "usage.json")
        refreshed = self.prepare(("lore",), refresh=True,
                                 settings={"install_model": True, "model_dir": model, "usage_config": usage})["completed"]["lore"]
        self.assertEqual(sum("install" in args for args in self.commands()), 1)
        self.assertNotIn("install_model", refreshed["settings"])
        self.assertEqual(refreshed["settings"]["model_dir"], model)
        self.assertEqual(refreshed["args"][-2:], ["--usage-config", usage])
        self.assertEqual(self.manager.status(self.project, servers=["lore"], check=True)["states"][0]["freshness"], "current")

    def test_key_signature_request_and_captured_definition_reuse(self):
        key = str(uuid.uuid4())
        first = self.prepare(key=key, settings={"resolve": True})
        job_dir = self.manager.root / "jobs" / key
        request = read(job_dir / "request.json")
        signature_fields = {"tools": ["sonar"], "project": str(self.project), "refresh": False, "settings": {"resolve": True}}
        self.assertEqual(first["signature"], digest(signature_fields))
        self.assertEqual(request, signature_fields | {"definitions": {"sonar": self.manager.registry()["sonar"]}})
        before = {path.name: path.read_bytes() for path in job_dir.iterdir()}
        self.manager.configure("sonar", {"tool_timeout_sec": 99}, expected_revision=self.manager.show("sonar")["revision"])
        with patch.object(jobs.subprocess, "Popen", side_effect=AssertionError("a reused job must not dispatch")):
            self.assertEqual(self.manager.prepare(["sonar"], self.project, key=key, settings={"resolve": True}), first)
            for change in ({"refresh": True, "settings": {"resolve": True}}, {"settings": {"resolve": False}}):
                with self.assertRaises(McpError) as caught:
                    self.manager.prepare(["sonar"], self.project, key=key, **change)
                self.assertEqual(caught.exception.code, "conflict")
        self.assertEqual({path.name: path.read_bytes() for path in job_dir.iterdir()}, before)
        self.assertEqual(self.manager.resolve(["sonar"], self.project)["states"][0]["status"], "prepared")

        self.manager.configure("sonar", {"args": [*self.manager.show("sonar")["args"], "--changed"]},
                               expected_revision=self.manager.show("sonar")["revision"])
        self.assertEqual(self.manager.resolve(["sonar"], self.project)["states"][0]["status"], "needs-preparation")

    def test_failed_refresh_and_wrong_roots_never_publish_over_previous_generation(self):
        self.prepare()
        manifest = self.manager.project(self.project) / "project.json"
        before = manifest.read_bytes()
        for control in ({"fail_commands": ["index-navigation"]}, {"index_root": str(self.root)},
                        {"health_root": str(self.root)}, {"health_error": True}):
            with self.subTest(control=control):
                self.control.write_text(json.dumps(control))
                failed = self.wait(self.manager.prepare(["sonar"], self.project, refresh=True))
                self.assertEqual(failed["status"], "failed", failed)
                self.assertEqual(manifest.read_bytes(), before)
                self.assertEqual(self.manager.resolve(["sonar"], self.project)["states"][0]["status"], "prepared")

    def test_freshness_stale_text_fallback_and_unavailable(self):
        self.prepare(("sonar", "lore"))
        self.control.write_text(json.dumps({"stale": True, "freshness": "stale", "text_only": True}))
        result = self.manager.status(self.project, check=True)
        self.assertEqual([state["freshness"] for state in result["states"]], ["stale", "stale"])
        self.control.write_text(json.dumps({"health_error": True}))
        result = self.manager.status(self.project, check=True)
        self.assertEqual([state["freshness"] for state in result["states"]], ["unavailable", "unavailable"])
        self.assertEqual([state["status"] for state in result["states"]], ["prepared", "prepared"])

    def test_partial_multitool_completion_and_concurrent_manifest_publication(self):
        self.control.write_text(json.dumps({"fail_commands": ["status"]}))
        failed = self.wait(self.manager.prepare(["sonar", "lore"], self.project))
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(set(failed["completed"]), {"sonar"})
        self.assertEqual(set(read(self.manager.project(self.project) / "project.json")["tools"]), {"sonar"})
        self.control.write_text("{}")
        sonar = self.manager.prepare(["sonar"], self.project, refresh=True)
        lore = self.manager.prepare(["lore"], self.project)
        self.assertEqual(self.wait(sonar)["status"], "completed")
        self.assertEqual(self.wait(lore)["status"], "completed")
        self.assertEqual(set(read(self.manager.project(self.project) / "project.json")["tools"]), {"sonar", "lore"})

    def test_interrupted_legacy_worker_is_inspected_without_replay(self):
        # Use a reaped Popen PID to create a real dead-worker job record.
        process = subprocess.Popen([sys.executable, "-c", "pass"]); process.wait()
        key = str(uuid.uuid4())
        request = {"tools": ["sonar"], "project": str(self.project), "refresh": False, "settings": {}}
        row = {"schema": 1, "id": key, "signature": digest(request), "status": "running", "pid": process.pid,
               "project": str(self.project), "tools": ["sonar"], "step": "sonar: index", "error": None,
               "created_at": 1, "updated_at": 2}
        path = self.manager.root / "jobs" / key / "status.json"
        write(path, row)
        before = path.read_bytes()
        with patch.object(jobs.subprocess, "Popen", side_effect=AssertionError("must not replay")):
            result = self.manager.prepare(["sonar"], self.project, key=key)
            self.assertEqual(result["status"], "interrupted")
            self.assertIn("not replayed", result["error"])
            self.assertEqual(self.manager.jobs(self.project)[0], result)
        self.assertEqual(path.read_bytes(), before)

    def test_detached_job_survives_initiating_client_exit(self):
        self.control.write_text(json.dumps({"index_delay": .3}))
        script = "import json,sys; from mcp_manager import Manager; print(json.dumps(Manager(sys.argv[1]).prepare(['sonar'],sys.argv[2])))"
        result = subprocess.run([sys.executable, "-c", script, str(self.manager.root), str(self.project)],
                                cwd=self.root, check=True, capture_output=True, text=True)
        row = json.loads(result.stdout)
        self.assertEqual(row["status"], "queued")
        self.assertEqual(self.wait(row)["status"], "completed")

    def test_bounded_probe_disconnect_and_timeout(self):
        self.prepare()
        row = self.manager.resolve(["sonar"], self.project)["servers"]["sonar"]
        self.control.write_text(json.dumps({"disconnect": True}))
        with self.assertRaises(McpError) as caught:
            probe(row, "sonar_status")
        self.assertEqual(caught.exception.code, "transport")
        self.control.write_text(json.dumps({"timeout": True}))
        with self.assertRaises(McpError) as caught:
            probe(row | {"startup_timeout_sec": .05}, "sonar_status")
        self.assertEqual(caught.exception.code, "timeout")

    def test_same_named_projects_and_worktrees_prepare_independently(self):
        other = self.root / "other" / "project"; other.mkdir(parents=True)
        worktree = self.root / "worktrees" / "project"; worktree.mkdir(parents=True)
        (self.project / ".git").mkdir()
        (worktree / ".git").write_text("gitdir: retained-worktree-fixture")
        records = []
        for project in (self.project, other, worktree):
            row = self.wait(self.manager.prepare(["sonar"], project))
            self.assertEqual(row["status"], "completed", row)
            records.append(row["completed"]["sonar"])
            child = project / "src"; child.mkdir()
            resolved = self.manager.resolve(["sonar"], child, check=True)
            self.assertEqual(resolved["project"], str(project))
            self.assertEqual(resolved["states"][0]["generation"], records[-1]["generation"])
            self.assertEqual(resolved["states"][0]["freshness"], "current")
            self.assertEqual(len(self.manager.jobs(project)), 1)
        self.assertEqual(len({row["generation"] for row in records}), 3)
        self.assertEqual(len({row["database"] for row in records}), 3)

    def test_invalid_stdio_responses_are_typed_and_failed_refresh_retains_manifest(self):
        self.prepare()
        manifest = self.manager.project(self.project) / "project.json"
        before = manifest.read_bytes()
        for control in ({"invalid_message": True}, {"invalid_result": True}, {"invalid_health": True}):
            with self.subTest(control=control):
                self.control.write_text(json.dumps(control))
                state = self.manager.status(self.project, servers=["sonar"], check=True)["states"][0]
                self.assertEqual(state["freshness"], "unavailable")
                failed = self.wait(self.manager.refresh(["sonar"], self.project))
                self.assertEqual(failed["status"], "failed")
                self.assertEqual(manifest.read_bytes(), before)

    def test_invalid_preparation_requests_fail_before_creating_store(self):
        manager = Manager(self.root / "absent")
        for kwargs in ({"key": "bad"}, {"key": ""}, {"settings": [1]}, {"refresh": "yes"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(McpError) as caught:
                manager.prepare(["sonar"], self.project, **kwargs)
            self.assertEqual(caught.exception.code, "validation")
        with self.assertRaises(McpError):
            manager.prepare(["sonar"], None)
        self.assertFalse(manager.home.exists())

    def test_missing_probe_executable_is_an_operational_error(self):
        with self.assertRaises(McpError) as caught:
            probe({"command": str(self.root / "absent-executable"), "args": []}, "status")
        self.assertEqual(caught.exception.code, "io")

    def test_unregistered_index_servers_cannot_be_prepared_or_installed(self):
        manager = Manager(self.root / "unregistered")
        with patch.object(jobs.subprocess, "Popen", side_effect=AssertionError("must not launch")):
            for tool in ("sonar", "lore"):
                with self.subTest(tool=tool), self.assertRaises(McpError) as caught:
                    manager.prepare([tool], self.project, settings={"install_model": True})
                self.assertEqual(caught.exception.code, "not_found")
        self.assertEqual(manager.catalog(), [])
        self.assertEqual(manager.jobs(), [])


if __name__ == "__main__":
    unittest.main()
