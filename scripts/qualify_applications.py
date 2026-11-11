#!/usr/bin/env python3
"""Qualify independent application extras from an offline wheelhouse.

Run with a Python that has pip. No source paths, runtime daemons, credentials or
network indexes are used by the installed probes.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv

SCENARIOS = {
    "base": ("aixand-agent-manager", set()),
    "prompts": ("aixand-agent-manager[prompts]", {"prompts"}),
    "skills": ("aixand-agent-manager[skills]", {"skills"}),
    "mcp": ("aixand-agent-manager[managed-mcp]", {"mcp"}),
    "resources": ("aixand-agent-manager[resources]", {"prompts", "skills", "mcp"}),
}


def probe(scenario):
    from agent_manager import Manager, ResourceError

    expected = SCENARIOS[scenario][1]
    for kind, module in (
        ("prompts", "prompt_manager"),
        ("skills", "skill_manager"),
        ("mcp", "mcp_manager"),
    ):
        assert (importlib.util.find_spec(module) is not None) == (kind in expected), (
            scenario,
            module,
        )
    assert importlib.util.find_spec("server_manager") is None
    assert importlib.util.find_spec("undercurrent") is None
    with tempfile.TemporaryDirectory(prefix="application-probe-") as temporary:
        root = Path(temporary)
        config = root / "providers.toml"
        config.write_text(
            '[providers.codex]\nexecutable="unused"\nnative_home='
            + json.dumps(str(root / "native"))
            + '\nauth_recovery="off"\n'
        )
        manager = Manager("codex", root=root / "agents", config=config)
        descriptor = {
            "schema": "agent-manager/application-1",
            "id": "probe",
            "roles": {"worker": {}},
        }
        stores = {
            kind: str(root / (kind + "-store")) for kind in ("prompts", "skills", "mcp")
        }
        bundles = {}
        if "prompts" in expected:
            pack = root / "prompts"
            pack.mkdir()
            (pack / "body.md").write_text("Installed prompt.")
            (pack / "pack.json").write_text(
                json.dumps(
                    {
                        "schema": "prompt-manager/pack-1",
                        "name": "probe",
                        "sections": {"body": {"file": "body.md"}},
                        "prompts": {
                            "main": {
                                "sections": [
                                    {"id": "body", "section": "body", "role": "system"}
                                ]
                            }
                        },
                    }
                )
            )
            bundles["prompts"] = pack
            descriptor["prompts"] = {"bundle": "prompts", "publisher_revision": 1}
            descriptor["roles"]["worker"].update(
                instructions="main", task_prompt="main"
            )
        if "skills" in expected:
            skill = root / "audit"
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                "---\nname: audit\ndescription: Installed test\n---\nCheck the input.\n"
            )
            bundles["audit"] = skill
            descriptor["skills"] = [
                {"id": "audit", "bundle": "audit", "publisher_revision": 1}
            ]
            descriptor["roles"]["worker"]["skills"] = ["audit"]
        if "mcp" in expected:
            definition = root / "tools.json"
            definition.write_text(
                json.dumps({"command": sys.executable, "args": ["-V"]})
            )
            bundles["tools"] = definition
            descriptor["mcp"] = [
                {"id": "tools", "bundle": "tools", "publisher_revision": 1}
            ]
            descriptor["roles"]["worker"]["mcp"] = ["tools"]
        app = manager.prepare_application(descriptor, bundles=bundles, stores=stores)
        assert all(row["status"] == "ready" for row in app.status()["resources"]), (
            app.status()
        )
        assert len(app.status()["resources"]) == len(expected)
        before = app.status()
        assert (
            manager.prepare_application(
                descriptor, bundles=bundles, stores=stores
            ).status()
            == before
        )
        layer = app.role_resources("worker")
        assert len(layer["skills"]) == int("skills" in expected)
        assert len(layer["mcp"]) == int("mcp" in expected)
        if "prompts" in expected:
            assert "Installed prompt." in app.render_task("worker")["output"]
        else:
            try:
                manager.prompts
            except ResourceError as error:
                assert (
                    error.code == "manager_missing"
                    and "aixand-agent-manager[prompts]" in str(error)
                )
            else:
                raise AssertionError("Missing Prompt Manager was not reported")
        assert not (root / "mcp-store/runtime-generations").exists()
    print(json.dumps({"scenario": scenario, "status": "passed"}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheelhouse", type=Path, nargs="?")
    parser.add_argument("--probe", choices=SCENARIOS, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.probe:
        probe(args.probe)
        return
    if args.wheelhouse is None or not args.wheelhouse.is_dir():
        parser.error("Supply an existing offline wheelhouse")
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("PYTHONPATH", "PYTHONHOME")
    }
    with tempfile.TemporaryDirectory(prefix="application-installs-") as temporary:
        base = Path(temporary)
        for scenario, (requirement, _) in SCENARIOS.items():
            home = base / scenario
            venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(
                home / "venv"
            )
            python = (
                home
                / "venv"
                / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "--python",
                    str(python),
                    "install",
                    "--no-index",
                    "--find-links",
                    str(args.wheelhouse.resolve()),
                    requirement,
                ],
                env=environment,
                check=True,
                stdout=subprocess.DEVNULL,
            )
            environment.update(
                HOME=str(home),
                XDG_CONFIG_HOME=str(home / "config"),
                XDG_STATE_HOME=str(home / "state"),
            )
            subprocess.run(
                [str(python), "-I", str(Path(__file__).resolve()), "--probe", scenario],
                cwd=home,
                env=environment,
                check=True,
            )


if __name__ == "__main__":
    main()
