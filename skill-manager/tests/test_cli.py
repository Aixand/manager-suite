import base64
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from skill_manager import SkillError
from skill_manager.cli import main
from test_manager import Fixture, skill_files


class CliTests(Fixture):
    def cli(self, *args, code=0, executable=False):
        command = [str(Path(sys.executable).parent / "skill-manager")] if executable else [sys.executable, "-m", "skill_manager"]
        result = subprocess.run([*command, "--home", str(self.home), *args], capture_output=True, text=True, timeout=20)
        self.assertEqual(code, result.returncode, result.stderr + result.stdout)
        return json.loads(result.stdout if result.stdout else result.stderr)

    def test_all_commands_with_binary_roundtrip_and_structured_errors(self):
        catalog = self.cli("catalog", "--json", executable=True)
        self.assertEqual(self.entry["id"], catalog["entries"][0]["id"])
        source = self.cli("source", "show", "local", "--json")
        self.assertEqual(self.registration["revision"], source["revision"])
        self.assertEqual(1, len(self.cli("source", "list", "--json")["sources"]))
        self.assertEqual("valid", self.cli("show", self.entry["id"], "--json")["status"])
        content = self.cli("show", self.entry["id"], "--file", "assets/sample.bin", "--json")
        self.assertEqual(skill_files()["assets/sample.bin"]["content"], base64.b64decode(content["content_b64"]))
        released = self.cli("release", self.entry["id"], "--version", "v1", "--expected-digest", self.entry["digest"], "--json")
        resolved = self.cli("resolve", released["reference"], "--json")
        self.assertEqual(released["digest"], resolved["digest"])
        self.assertEqual([released], self.cli("releases", "--source", "local", "--name", "csv-audit", "--json")["releases"])
        self.assertTrue(self.cli("validate", released["reference"], "--json")["valid"])
        self.assertEqual([], self.cli("diff", self.entry["id"], released["reference"], "--json")["changed"])
        self.assertEqual("exported", self.cli("export", released["reference"], str(self.root / "csv-audit"), "--json")["status"])
        self.assertEqual("identical", self.cli("export", released["reference"], str(self.root / "csv-audit"), "--json")["status"])
        new_file = self.root / "binary"
        new_file.write_bytes(b"\xff\x00CLI change")
        saved = self.cli("draft", "save", self.entry["id"], "assets/sample.bin", "--file", str(new_file),
                         "--expected-digest", self.entry["digest"], "--json")
        self.assertNotEqual(self.entry["digest"], saved["digest"])
        error = self.cli("draft", "save", self.entry["id"], "assets/sample.bin", "--file", str(new_file),
                         "--expected-digest", self.entry["digest"], "--json", code=2)
        self.assertEqual("conflict", error["error"]["code"])
        removed = self.cli("source", "unregister", "local", "--expected-revision", source["revision"], "--json")
        self.assertEqual("local", removed["source_id"])
        self.assertEqual(released["digest"], self.cli("resolve", released["reference"], "--json")["digest"])

    def test_source_register_and_draft_create_json_file(self):
        root = self.root / "new-source"
        root.mkdir()
        source = self.cli("source", "register", "new", str(root), "--writable", "--label", "CLI source", "--json")
        specs = {p: {"content_b64": base64.b64encode(s["content"]).decode(), "executable": s["executable"]}
                 for p, s in skill_files("cli-skill").items()}
        json_file = self.root / "files.json"
        json_file.write_text(json.dumps(specs))
        created = self.cli("draft", "create", "new", "cli-skill", "--files", "@" + str(json_file), "--json")
        self.assertTrue(self.cli("validate", created["id"], "--json")["valid"])
        self.assertEqual("conflict", self.cli("source", "register", "new", str(root), "--expected-revision", "none", "--json", code=2)["error"]["code"])
        row = self.cli("source", "register", "new", str(root), "--expected-revision", source["revision"], "--json")
        self.assertFalse(row["writable"])
        self.assertEqual("unsupported", self.cli("draft", "save", created["id"], "SKILL.md", "--file", str(root / "cli-skill" / "SKILL.md"),
                                               "--expected-digest", created["digest"], "--json", code=3)["error"]["code"])

    def test_invalid_metadata_exit_code_and_invalid_json_inputs(self):
        current = self.manager.save_file(self.entry["id"], "SKILL.md", b"broken", expected_digest=self.entry["digest"])
        result = self.cli("validate", current["id"], "--json", code=2)
        self.assertFalse(result["valid"])
        self.assertEqual("not_found", self.cli("resolve", "local/csv-audit@missing", "--json", code=2)["error"]["code"])
        for specs in ('{"x":1,"x":2}', '{"SKILL.md":{"content_b64":"%%%"}}', '{"SKILL.md":{"content_b64":"AA==","executable":1}}'):
            self.assertEqual("validation", self.cli("draft", "create", "local", "other", "--files", specs, "--json", code=2)["error"]["code"])

    def test_operational_and_uncertain_exit_codes(self):
        with patch("skill_manager.cli.dispatch", side_effect=SkillError("io", "failure")), patch("sys.stderr"):
            self.assertEqual(1, main(["catalog", "--json"]))
        with patch("skill_manager.cli.dispatch", side_effect=SkillError("uncertain", "lost", uncertain=True)), patch("sys.stderr"):
            self.assertEqual(4, main(["catalog", "--json"]))

    def test_missing_required_guard_is_a_structured_validation_error(self):
        error = self.cli("release", self.entry["id"], "--version", "v1", "--json", code=2)
        self.assertEqual("validation", error["error"]["code"])
        self.assertIn("expected-digest", error["error"]["message"])
