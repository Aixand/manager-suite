"""Exercise one product's offline lifecycle in disposable stores."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/dev"))
from releases import MANIFEST, validate
from qualify_product_installation import upgrade_fixture


def qualify(artifact, report=None):
    artifact, manifest = validate(Path(artifact).resolve())
    selection = manifest["selection"]
    if manifest["kind"] != "product" or set(manifest["products"]) != {selection}:
        raise ValueError("Single-product qualification needs exactly one product")
    with tempfile.TemporaryDirectory(prefix="manager-product-lifecycle-") as temporary:
        base = Path(temporary).resolve()
        helper = base / "helper"
        shutil.copytree(
            ROOT / "scripts/dev",
            helper,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        prefix = base / "software"
        data = base / "data"
        data.mkdir()
        (data / "keep.txt").write_text("preserve")
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(
                (
                    "AGENT_",
                    "AIXAND_",
                    "CONVERSATION_",
                    "MCP_",
                    "PROMPT_",
                    "SKILL_",
                    "UC_",
                    "UNDERCURRENT_",
                )
            )
        }
        environment.update(
            HOME=str(base / "home"),
            XDG_DATA_HOME=str(data),
            AIXAND_MANAGER_ROOT=str(data),
            HTTP_PROXY="http://127.0.0.1:1",
            HTTPS_PROXY="http://127.0.0.1:1",
            PIP_NO_INDEX="1",
            UV_OFFLINE="1",
            PYTHONPATH=str(base / "no-source"),
            PYTHONHOME=str(base / "no-python"),
        )

        def consumer(action, *arguments, success=True):
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(helper / "consumer.py"),
                    action,
                    *map(str, arguments),
                ],
                cwd=base,
                env=environment,
                capture_output=True,
                text=True,
                timeout=600,
            )
            if (result.returncode == 0) != success:
                raise RuntimeError(
                    f"Unexpected {action} outcome: {result.stdout[-3000:]}\n{result.stderr[-3000:]}"
                )
            return json.loads(result.stdout) if success else None

        conflict = base / "unowned"
        conflict.mkdir()
        (conflict / "keep.txt").write_text("unrelated")
        consumer("install", "--artifact", artifact, "--prefix", conflict, success=False)
        assert (conflict / "keep.txt").read_text() == "unrelated"
        consumer("install", "--artifact", artifact, "--prefix", prefix)
        receipt = json.loads(
            (prefix / "installation-receipt.json").read_text(encoding="utf-8")
        )
        assert set(receipt["products"]) == {selection}
        consumer("install", "--artifact", artifact, "--prefix", prefix)
        consumer("reinstall", "--artifact", artifact, "--prefix", prefix)
        for command in manifest["products"][selection]["commands"]:
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(helper / "consumer.py"),
                    "exec",
                    "--prefix",
                    str(prefix),
                    "--command",
                    command,
                    "--",
                    "--help",
                ],
                cwd=base,
                env=environment,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode:
                raise RuntimeError(result.stderr)

        def prompt(*arguments):
            return consumer(
                "exec",
                "--prefix",
                prefix,
                "--command",
                "prompt-manager",
                "--",
                "--home",
                data / "prompts",
                *arguments,
            )

        if selection == "manager-suite/prompt-manager":
            pack = base / "pack"
            pack.mkdir()
            (pack / "body.md").write_text("Retained prompt across upgrade.")
            (pack / "pack.json").write_text(
                json.dumps(
                    {
                        "schema": "prompt-manager/pack-1",
                        "name": "retained",
                        "sections": {"body": {"file": "body.md"}},
                        "prompts": {
                            "main": {"sections": [{"id": "body", "section": "body"}]}
                        },
                    }
                )
            )
            prompt("release", pack, "--version", "1")
        retained = {
            p.relative_to(data): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in data.rglob("*")
            if p.is_file()
        }
        corrupt = base / "corrupt"
        shutil.copytree(artifact, corrupt)
        member = next((corrupt / "objects").iterdir())
        member.write_bytes(member.read_bytes() + b"corrupt")
        current = (prefix / "installation-receipt.json").read_bytes()
        consumer("upgrade", "--artifact", corrupt, "--prefix", prefix, success=False)
        assert (prefix / "installation-receipt.json").read_bytes() == current
        candidate = upgrade_fixture(artifact, base, selection)
        consumer("upgrade", "--artifact", candidate, "--prefix", prefix)
        upgraded = json.loads(
            (prefix / "installation-receipt.json").read_text(encoding="utf-8")
        )
        assert (
            upgraded["products"][selection]["product"]["version"]
            != receipt["products"][selection]["product"]["version"]
        )
        assert (data / "keep.txt").read_text() == "preserve"
        assert all(
            hashlib.sha256((data / path).read_bytes()).hexdigest() == value
            for path, value in retained.items()
        )
        if selection == "manager-suite/prompt-manager":
            # Render returns text rather than JSON, so invoke it separately.
            rendered = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(helper / "consumer.py"),
                    "exec",
                    "--prefix",
                    str(prefix),
                    "--command",
                    "prompt-manager",
                    "--",
                    "--home",
                    str(data / "prompts"),
                    "render",
                    "retained@1/main",
                ],
                cwd=base,
                env=environment,
                capture_output=True,
                text=True,
                timeout=120,
            )
            assert (
                rendered.returncode == 0
                and "Retained prompt across upgrade." in rendered.stdout
            )
        if manifest["products"][selection]["stateful"]:
            consumer(
                "rollback", "--prefix", prefix, "--product", selection, success=False
            )
        consumer("uninstall", "--prefix", prefix, "--product", selection, success=False)
        consumer("uninstall", "--prefix", prefix, "--product", selection, "--stopped")
        assert not consumer("inspect", "--prefix", prefix)["products"]
        assert (data / "keep.txt").read_text() == "preserve"
        assert all(
            hashlib.sha256((data / path).read_bytes()).hexdigest() == value
            for path, value in retained.items()
        )
        result = {
            "schema": 1,
            "kind": "single-product-lifecycle",
            "product": selection,
            "target": manifest["target"],
            "manifest_sha256": hashlib.sha256(
                (artifact / MANIFEST).read_bytes()
            ).hexdigest(),
            "passed": True,
            "checks": [
                "unowned-prefix",
                "install",
                "idempotent-install",
                "reinstall",
                "commands",
                "corruption-rejected",
                "successful-upgrade-assembly-fixture",
                "rollback-guard",
                "stopped-guard",
                "uninstall",
                "data-retention",
            ],
        }
    if report:
        with Path(report).open("x") as stream:
            stream.write(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args()
    print(json.dumps(qualify(arguments.artifact, arguments.report)))
