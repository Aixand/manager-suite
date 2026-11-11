import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

from mcp_manager import Manager, digest
from mcp_manager.cli import main


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mcp-cli-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"; self.project.mkdir()
        self.home = self.root / "store"
        self.manager = Manager(self.home)

    def call(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--home", str(self.home), *args, "--json"])
        return code, json.loads(output.getvalue())

    def test_catalog_add_show_guarded_partial_configure_resolve_and_jobs(self):
        self.assertEqual(self.call("list"), (0, []))
        code, status = self.call("status", "--project", str(self.project))
        self.assertEqual(code, 0)
        self.assertEqual(status["states"], [])
        self.assertFalse(self.home.exists())
        definition = self.root / "definition.json"
        definition.write_text(json.dumps({"command": sys.executable, "args": ["-V"], "env_vars": ["TOKEN"]}))
        code, created = self.call("add", "docs", "--definition", "@" + str(definition))
        self.assertEqual(code, 0)
        self.assertEqual(self.call("show", "docs")[1]["revision"], created["revision"])
        self.assertEqual(self.call("add", "docs", "--definition", "{}")[0], 2)
        code, updated = self.call("configure", "docs", "--definition", '{"args":[]}', "--expected-revision", created["revision"])
        self.assertEqual(code, 0)
        self.assertEqual(updated["command"], str(Path(sys.executable).absolute()))
        self.assertEqual(self.call("configure", "docs", "--definition", '{"args":["-V"]}', "--expected-revision", created["revision"])[0], 2)
        code, resolution = self.call("resolve", "--servers", '["docs","docs"]', "--cwd", str(self.project), "--no-project", "--strict")
        self.assertEqual(code, 0)
        self.assertEqual(list(resolution["servers"]), ["docs"])
        self.assertIsNone(resolution["project"])
        self.assertEqual(self.call("jobs", "--project", str(self.project)), (0, []))

    def test_structured_validation_not_found_and_io_exits_without_tracebacks(self):
        cases = (("show", "missing"), ("job", "invalid-uuid"), ("prepare", "sonar", "--project", str(self.project), "--key", "bad"),
                 ("configure", "docs", "--definition", "{}"), ("resolve", "--servers", "null", "--cwd", str(self.project)),
                 ("add", "docs", "--definition", '{"command":"x","command":"y"}'),
                 ("add", "docs", "--definition", '{"command":"x","tool_timeout_sec":NaN}'),
                 ("add", "docs", "--definition", '"not an object"'),
                 ("add", "docs", "--definition", '{"command":"\\ud800"}'))
        for args in cases:
            with self.subTest(args=args):
                code, result = self.call(*args)
                self.assertEqual(code, 2)
                self.assertEqual(result["schema"], "mcp-manager/error-1")
                self.assertFalse(result["error"]["uncertain"])
        code, result = self.call("add", "docs", "--definition", "@" + str(self.root / "absent"))
        self.assertEqual(code, 1)
        self.assertEqual(result["error"]["code"], "io")
        self.assertFalse(self.home.exists())

    def test_prepare_wait_refresh_failure_background_and_retained_inspection(self):
        control = self.root / "control.json"
        fixture = Path(__file__).parent / "fixtures" / "preset.py"
        self.manager.add("sonar", {"kind": "sonar", "command": sys.executable, "args": [str(fixture), "--fixture-control", str(control),
            "--fixture-log", str(self.root / "log")]})
        code, first = self.call("prepare", "sonar", "--project", str(self.project), "--resources", "yaml", "--resolve")
        self.assertEqual(code, 0)
        self.assertEqual(first["status"], "completed")
        self.assertEqual(self.call("job", first["id"]), (0, first))
        manifest = self.manager.project(self.project) / "project.json"
        before = manifest.read_bytes()
        control.write_text('{"fail_commands":["index-navigation"]}')
        code, failed = self.call("refresh", "sonar", "--project", str(self.project))
        self.assertEqual(code, 1)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(manifest.read_bytes(), before)
        control.write_text('{"index_delay":0.2}')
        command = [sys.executable, "-B", "-m", "mcp_manager", "--home", str(self.home), "refresh", "sonar",
                   "--project", str(self.project), "--background", "--json"]
        result = subprocess.run(command, cwd=self.root, capture_output=True, text=True, check=True)
        background = json.loads(result.stdout)
        self.assertEqual(background["status"], "queued")
        deadline = time.monotonic() + 10
        while background["status"] in ("queued", "running") and time.monotonic() < deadline:
            time.sleep(.02)
            background = self.manager.job(background["id"])
        self.assertEqual(background["status"], "completed")
        self.assertEqual(len(self.call("jobs")[1]), 3)
        code, status = self.call("status", "--project", str(self.project), "--servers", '["sonar"]', "--check")
        self.assertEqual(code, 0)
        self.assertEqual(status["states"][0]["freshness"], "current")

    def target(self):
        return {"schema": "mcp-manager/runtime-target-1", "connector_id": "fixture", "backend_id": "fixture",
            "connection_ref": {"connection_id": "connection"}, "runtime_ref": {"owner": "fixture", "id": "runtime"},
            "native_session_id": "session", "selection_ref": "saved", "selection_digest": digest(["docs"]), "prepared_generations": []}

    def test_runtime_explicit_factory_scope_exit_codes_and_operation_inspection(self):
        fixture_dir = str(Path(__file__).parent / "fixtures")
        target = self.root / "target.json"; target.write_text(json.dumps(self.target()))
        args = ("--connector", "backend:factory", "--target", "@" + str(target))
        with patch.object(sys, "path", [fixture_dir, *sys.path]):
            code, caps = self.call("runtime", "capabilities", *args)
            self.assertEqual(code, 0)
            self.assertEqual(caps["schema"], "mcp-manager/runtime-capabilities-1")
            self.assertEqual(self.call("runtime", "status", *args)[0], 2)
            code, status = self.call("runtime", "status", *args, "--scope", "session")
            self.assertEqual(code, 0)
            self.assertEqual(status["servers"][0]["connection_status"], "failed")
            for options, expected in (({}, 0), ({"preserve_selection": False}, 3), ({"lost_reply": True}, 4), ({"status": "failed"}, 1)):
                operation_id = str(uuid.uuid4())
                code, result = self.call("runtime", "reconnect", *args, "--scope", "session", "--operation-id", operation_id,
                    "--expected-selection-digest", self.target()["selection_digest"], "--options", json.dumps(options))
                self.assertEqual(code, expected)
                if result["status"] != "unsupported":
                    self.assertEqual(self.call("runtime", "operation", operation_id), (expected, result))
            self.assertEqual(self.call("runtime", "capabilities", "--connector", "unavailable:factory", "--target", "{}")[0], 2)


if __name__ == "__main__":
    unittest.main()
