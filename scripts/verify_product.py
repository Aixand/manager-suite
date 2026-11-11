"""Exercise installed APIs in temporary explicit stores, without providers or Suite."""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import tempfile


def prompt(root):
    from prompt_manager import Manager

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
    manager = Manager(home=root / "prompt-store", packs=[])
    manager.release(pack, "1")
    (pack / "body.md").write_text("Changed draft.")
    assert "Installed prompt." in manager.render("probe@1/main")["output"]
    assert "Changed draft." in manager.render(pack, prompt="main")["output"]
    return pack


def skill(root):
    from skill_manager import Manager

    directory = root / "skills/audit"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "---\nname: audit\ndescription: Installed test\n---\nCheck input.\n"
    )
    (directory / "asset.bin").write_bytes(b"\x00\x01test")
    manager = Manager(root / "skill-store")
    manager.register_source(
        "probe", [directory.parent], owner={"kind": "installer-test"}
    )
    entry = manager.catalog()["entries"][0]
    publication = manager.release(entry["id"], "1", expected_digest=entry["digest"])
    resolved = manager.resolve(publication["reference"])
    export = root / "exported/audit"
    export.parent.mkdir()
    assert manager.export(resolved["reference"], export)["status"] == "exported"
    assert (export / "asset.bin").read_bytes() == b"\x00\x01test"
    return directory


def mcp(root):
    from mcp_manager import Manager

    manager = Manager(root / "mcp-store")
    working_directory = root / "München 世界"
    working_directory.mkdir()
    created = manager.configure(
        "probe",
        {"command": sys.executable, "args": ["-V"], "cwd": str(working_directory)},
    )
    assert manager.show("probe")["revision"] == created["revision"]
    resolved = manager.resolve(["probe"], root, project=None, strict=True)
    assert resolved["states"][0]["status"] == "configured"
    assert resolved["servers"]["probe"]["args"] == ["-V"]
    definition = root / "tools.json"
    definition.write_text(json.dumps({"command": sys.executable, "args": ["-V"]}))
    return definition


def agent(root, bundles):
    from agent_manager import Manager

    config = root / "providers.toml"
    config.write_text(
        '[providers.codex]\nexecutable="unused"\nnative_home='
        + json.dumps(str(root / "native"))
        + "\n"
    )
    manager = Manager("codex", root=root / "agents", config=config)
    manager.new_profile("probe")
    target = manager.resolve_profile("probe")
    assert (
        manager.resolve_agent_target(target.environment_id)["environment_id"]
        == target.environment_id
    )
    assert any(
        row["environment_id"] == target.environment_id
        for row in manager.agent_targets()
    )
    assert manager.home("probe").is_dir()


def verify(root):
    profile = json.loads(Path(__file__).with_name("profile.json").read_text())
    site = Path(sysconfig.get_path("purelib")).resolve()
    assert sys.version.split()[0] == profile["python"]
    for key, (distribution, module) in profile["all_packages"].items():
        if key in profile["packages"]:
            loaded = importlib.import_module(module)
            assert Path(loaded.__file__).resolve().is_relative_to(site), module
            importlib.metadata.version(distribution)
        else:
            assert importlib.util.find_spec(module) is None, module
    checks, bundles = [], {}
    if "prompt-manager" in profile["packages"]:
        bundles["prompts"] = prompt(root)
        checks.append("immutable-prompt-release")
    if "skill-manager" in profile["packages"]:
        bundles["audit"] = skill(root)
        checks.append("skill-release-and-binary-export")
    if "mcp-manager" in profile["packages"]:
        bundles["tools"] = mcp(root)
        checks.append("mcp-definition-resolution")
    if "conversation-manager" in profile["packages"]:
        from conversation_manager.demo import demo

        result = asyncio.run(demo(str(root / "conversation")))
        assert result["status"] == "completed" and result["reattached_same_runtime"], (
            result
        )
        import websockets
        import jsonschema

        checks.append("retained-fixture-session-and-reconnect")
    if "agent-manager" in profile["packages"]:
        agent(root, bundles)
        checks.append("native-profile-and-environment-discovery")
    return {
        "verified": True,
        "product": profile["product"],
        "checks": checks,
        "installed_imports": True,
        "python": sys.version.split()[0],
    }


if __name__ == "__main__":
    # macOS /var and /tmp are aliases; source checks require literal ancestors.
    with tempfile.TemporaryDirectory(
        prefix="manager-product-check-",
        dir="/tmp" if sys.platform == "darwin" else None,
    ) as temporary:
        print(json.dumps(verify(Path(temporary).resolve()), sort_keys=True))
