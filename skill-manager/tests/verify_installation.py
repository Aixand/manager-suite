"""Run from an unrelated directory after installing only the built wheel."""
import argparse
import ast
import hashlib
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import skill_manager
from skill_manager import Manager, SkillError
import yaml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    module = Path(skill_manager.__file__).resolve()
    assert module.is_relative_to(Path(sys.prefix).resolve()), module
    assert "PYTHONPATH" not in os.environ
    assert not (module.parent / ".." / ".." / ".." / "src").resolve().exists()
    installed = sorted(d.metadata["Name"] for d in importlib.metadata.distributions())
    required = {"aixand-skill-manager", "aixand-prompt-manager", "aixand-lib-filesystem", "PyYAML"}
    assert required <= set(installed) <= required | {"pip", "setuptools", "wheel", "aixand-lib-github-source"}, installed
    methods = ("register_source", "source", "sources", "unregister_source", "catalog", "show", "read_file",
               "create_draft", "save_file", "validate", "release", "resolve", "diff", "export", "releases")
    signatures = {name: str(inspect.signature(getattr(Manager, name))) for name in methods}
    assert inspect.signature(Manager.register_source).parameters["expected_revision"].default is None
    for method, guard in (("save_file", "expected_digest"), ("release", "expected_digest"),
                          ("unregister_source", "expected_revision")):
        assert inspect.signature(getattr(Manager, method)).parameters[guard].default is inspect.Parameter.empty
    forbidden = {"agent_manager", "server_manager", "mcp_manager", "aixand_suite",
                 "undercurrent", "carrier", "runner", "resource_sources", "aixand_platform"}
    assert not importlib.metadata.requires("aixand-prompt-manager")
    for file in module.parent.glob("*.py"):
        tree = ast.parse(file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(alias.name.split(".")[0] in forbidden for alias in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in forbidden
    executable = Path(sys.executable).parent / "skill-manager"
    version = subprocess.run([str(executable), "--version"], capture_output=True, text=True, check=True).stdout.strip()
    assert version == skill_manager.__version__ == "0.1.0b0"
    assert importlib.metadata.version("aixand-skill-manager") == "0.1.0b0"
    with tempfile.TemporaryDirectory(prefix="installed-skill-public-api-") as temporary:
        root = Path(temporary)
        manager = Manager(root / "home")
        assert manager.catalog()["entries"] == []
        assert not manager.home.exists()
        manager.register_source("example", [args.fixtures / "examples"], owner={"kind": "fixture"})
        entry = manager.catalog()["entries"][0]
        assert entry["status"] == "valid" and not entry["editable"]
        publication = manager.release(entry["id"], "v1", expected_digest=entry["digest"])
        pinned = manager.resolve(publication["reference"])
        exported = manager.export(pinned["reference"], root / pinned["name"])
        identical = manager.export(pinned["reference"], root / pinned["name"])
        assert exported["status"] == "exported" and identical["status"] == "identical"
        assert exported["digest"] == pinned["digest"]
        binary = manager.read_file(pinned["reference"], "assets/badge.png")
        assert binary == (args.fixtures / "examples" / "csv-audit" / "assets" / "badge.png").read_bytes()
        command = subprocess.run([str(executable), "--home", str(manager.home), "catalog", "--json"],
                                 capture_output=True, text=True, check=True)
        assert json.loads(command.stdout)["entries"][0]["digest"] == entry["digest"]
        observation = {"reference": publication["reference"], "digest": publication["digest"],
                       "files": len(pinned["manifest"]["files"]), "directories": pinned["manifest"]["directories"],
                       "binary_digest": "sha256:" + hashlib.sha256(binary).hexdigest(),
                       "export": exported["status"], "repeat_export": identical["status"]}
    error = SkillError("conflict", "fixture", recovery={"reference": "example/csv-audit@v1"})
    assert set(error.to_dict()) == {"code", "message", "uncertain", "operation_id", "recovery"}
    report = {"python": sys.version.split()[0], "package": version, "PyYAML": yaml.__version__,
              "module": str(module), "installed": installed, "signatures": signatures,
              "fixture": observation, "PYTHONPATH": "unset", "checks": "passed"}
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
