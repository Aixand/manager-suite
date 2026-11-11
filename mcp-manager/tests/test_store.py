import hashlib
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from mcp_manager import AUTO, PREFIX, Manager, McpError, definition, digest


class StoreTests(unittest.TestCase):
    def test_configuring_and_resolving_an_interpreter_preserves_its_virtual_environment(self):
        import subprocess
        import venv
        directory = self.root / 'isolated python'
        venv.EnvBuilder(with_pip=False, symlinks=os.name != 'nt').create(directory)
        executable = directory / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        self.manager.configure('isolated', {'command': str(executable)})
        result = self.manager.resolve(['isolated'], self.project)
        command = next(iter(result['servers'].values()))['command']
        prefix = subprocess.check_output([command, '-c', 'import sys; print(sys.prefix)'], text=True).strip()
        self.assertEqual(Path(prefix), directory)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mcp-store-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.manager = Manager(self.root / "store")

    def test_read_only_absent_store_and_empty_selection_do_not_write(self):
        self.assertEqual(self.manager.catalog(), [])
        self.assertEqual(self.manager.status(self.project)["states"], [])
        for name in ("sonar", "lore"):
            with self.subTest(name=name), self.assertRaises(McpError) as caught:
                self.manager.show(name)
            self.assertEqual(caught.exception.code, "not_found")
        self.assertEqual(self.manager.jobs(), [])
        self.assertEqual(self.manager.resolve([], self.project)["servers"], {})
        self.assertFalse(self.manager.home.exists())
        self.manager.home.mkdir()
        registry = self.manager.home / "registry.json"
        registry.write_text("invalid")
        self.assertEqual(self.manager.resolve([], self.project)["states"], [])
        with self.assertRaises(McpError):
            self.manager.catalog()

    def test_home_precedence_and_canonical_identity(self):
        with patch.dict(os.environ, {"MCP_MANAGER_HOME": str(self.root / "override"),
                                     "AGENT_MANAGER_HOME": str(self.root / "agents")}):
            self.assertEqual(Manager().root, self.root / "override")
            self.assertEqual(Manager(self.root / "explicit").root, self.root / "explicit")
        with patch.dict(os.environ, {"AGENT_MANAGER_HOME": str(self.root / "agents")}):
            with patch.dict(os.environ):
                os.environ.pop("MCP_MANAGER_HOME", None)
                from mcp_manager._paths import default_root
                self.assertEqual(Manager().root, default_root().resolve())
        self.assertEqual(self.manager.store_id, digest({"kind": "mcp", "home": str(self.manager.root)}))

    def test_legacy_spaced_unicode_digest_is_frozen(self):
        value = {"env_vars": ["TOKEN"], "name": "München", "settings": {"resources": "yaml", "resolve": True}}
        self.assertEqual(digest(value), "sha256:0ef298758134cdbcc6eb9fd075609eba35238296f07cd73ef209966864b4967e")
        compact = "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                                       separators=(",", ":")).encode()).hexdigest()
        self.assertNotEqual(digest(value), compact)

    def test_schema_one_registry_is_read_without_rewriting(self):
        self.manager.home.mkdir()
        path = self.manager.home / "registry.json"
        saved = {"schema": 1, "servers": {
            "docs": {"command": sys.executable, "args": ["-V"], "env_vars": ["DOCS_TOKEN"]},
            "sonar": {"kind": "sonar", "command": sys.executable, "args": ["--custom"]}}}
        path.write_text(json.dumps(saved, indent=3))
        before = path.read_bytes()
        row = self.manager.show("docs")
        self.assertEqual(row["revision"], digest(definition("docs", saved["servers"]["docs"])))
        resolved = self.manager.resolve(["docs"], self.project, strict=True)
        self.assertEqual(resolved["servers"]["docs"]["server_name"], PREFIX + "docs")
        self.assertEqual([row["name"] for row in self.manager.catalog()], ["docs", "sonar"])
        self.assertEqual(self.manager.show("sonar")["args"], ["--custom"])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(set(self.manager.home.iterdir()), {path})

    def test_definition_edits_are_guarded_and_project_adapters_require_registration(self):
        created = self.manager.configure("docs", {"command": sys.executable, "args": ["-V"]})
        with self.assertRaises(McpError) as caught:
            self.manager.configure("docs", {"args": []})
        self.assertEqual(caught.exception.code, "conflict")
        changed = self.manager.configure("docs", {"args": []}, expected_revision=created["revision"])
        self.assertEqual(changed["command"], str(Path(sys.executable).absolute()))
        with self.assertRaises(McpError):
            self.manager.configure("docs", {"args": ["-V"]}, expected_revision=created["revision"])
        sonar = self.manager.add("sonar", {"kind": "sonar", "command": sys.executable})
        self.assertEqual(sonar["kind"], "sonar")
        with self.assertRaises(McpError):
            self.manager.configure("sonar", {"command": sys.executable})
        self.manager.configure("sonar", {"command": sys.executable}, expected_revision=self.manager.show("sonar")["revision"])
        self.assertNotIn("revision", json.loads((self.manager.home / "registry.json").read_text())["servers"]["docs"])

    def test_unicode_definition_round_trip_retains_revision_and_edit_guard(self):
        arguments = ["--label", "München 世界"]
        created = self.manager.configure("unicode", {"command": sys.executable, "args": arguments})
        shown = self.manager.show("unicode")
        self.assertEqual(shown["args"], arguments)
        self.assertEqual(shown["revision"], created["revision"])
        changed = self.manager.configure("unicode", {"args": ["--version"]},
                                         expected_revision=created["revision"])
        self.assertEqual(self.manager.show("unicode")["revision"], changed["revision"])

    def test_resolution_order_general_context_and_error_states(self):
        for identifier in ("first", "second"):
            self.manager.configure(identifier, {"command": sys.executable})
        self.manager.configure("missing", {"command": "mcp-fixture-nonexistent-command"})
        for preset in ("sonar", "lore"):
            self.manager.add(preset, {"kind": preset, "command": sys.executable})
        result = self.manager.resolve(["second", "first", "second", "sonar"], self.project, project=None, strict=True)
        self.assertEqual([row["name"] for row in result["states"]], ["second", "first", "sonar"])
        self.assertEqual(list(result["servers"]), ["second", "first"])
        self.assertEqual(result["states"][-1]["status"], "needs-project")
        self.assertEqual(result["digest"], digest({key: value for key, value in result.items() if key != "digest"}))
        self.assertEqual(self.manager.resolve(["missing"], self.project)["states"][0]["status"], "missing-executable")
        self.assertEqual(self.manager.status(self.project)["states"][0]["status"], "needs-preparation")
        with self.assertRaisesRegex(McpError, "executable not found"):
            self.manager.resolve(["missing"], self.project, strict=True)
        with self.assertRaisesRegex(McpError, "Unknown MCP"):
            self.manager.resolve(["unknown"], self.project)
        with self.assertRaisesRegex(McpError, "64"):
            self.manager.resolve(["first"] * 65, self.project)

    def test_server_names_do_not_infer_project_adapter_defaults(self):
        for name in ("sonar", "lore"):
            created = self.manager.add(name, {"command": sys.executable})
            self.assertEqual(created["kind"], "stdio")
        self.assertEqual(self.manager.status(self.project)["states"], [])

    def test_environment_references_and_validation_remain_legacy(self):
        row = self.manager.configure("docs", {"command": sys.executable, "env_vars": ["DOCS_TOKEN"]})
        with patch.dict(os.environ, DOCS_TOKEN="fixture-secret"):
            result = self.manager.resolve(["docs"], self.project)
        self.assertNotIn("fixture-secret", json.dumps(result))
        for patch_value in ({"env": {"TOKEN": "secret"}}, {"env_vars": ["invalid=value"]},
                            {"startup_timeout_sec": True}, {"args": ["nul\0"]}, {"kind": "sonar"}):
            with self.subTest(patch_value=patch_value), self.assertRaises(McpError):
                self.manager.configure("docs", patch_value, expected_revision=row["revision"])

    def test_competing_definition_edits_compare_revision_under_shared_lock(self):
        created = self.manager.configure("docs", {"command": sys.executable})
        def change(timeout):
            try:
                return Manager(self.manager.home).configure("docs", {"tool_timeout_sec": timeout},
                    expected_revision=created["revision"])
            except McpError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(change, [30, 45]))
        self.assertEqual(sum(isinstance(row, dict) for row in results), 1)
        self.assertEqual(sum(row == "conflict" for row in results), 1)
        self.assertIn(self.manager.show("docs")["tool_timeout_sec"], (30, 45))

    def test_project_lookup_and_worktrees_use_exact_paths(self):
        (self.project / ".git").mkdir()
        child = self.project / "child"; child.mkdir()
        nested = child / "nested"; nested.mkdir()
        self.assertEqual(self.manager.context(nested), self.project)
        prepared = self.manager.project(child) / "project.json"
        prepared.parent.mkdir(parents=True); prepared.write_text('{"schema":1,"tools":{}}')
        self.assertEqual(self.manager.context(nested, AUTO), child)
        self.assertEqual(self.manager.context(nested, self.project), self.project)
        self.assertIsNone(self.manager.context(nested, None))
        worktree = self.root / "worktree"; worktree.mkdir(); (worktree / ".git").write_text("gitdir: fixture")
        other = self.root / "other" / "project"; other.mkdir(parents=True)
        self.assertEqual(self.manager.context(worktree), worktree)
        self.assertEqual(self.manager.project(self.project).name, hashlib.sha256(str(self.project).encode()).hexdigest())
        self.assertEqual(len({self.manager.project(p) for p in (self.project, other, worktree)}), 3)


if __name__ == "__main__":
    unittest.main()
