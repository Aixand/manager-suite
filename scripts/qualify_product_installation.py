"""Qualify independent products, aggregate ownership, offline installs and isolation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/dev"))
from core import digest, write_json
from releases import MANIFEST, object_file, product_hash, seal, validate
from manager_profiles import COMMANDS


def upgrade_fixture(artifact, base, selection="manager-suite/prompt-manager"):
    """Marked installer assembly fixture, not a future application/schema release."""
    candidate = base / "upgrade-fixture"
    shutil.copytree(artifact, candidate)
    _, manifest = validate(candidate)
    marker = base / "fixture.json"
    write_json(
        marker,
        {
            "purpose": "installer-lifecycle-test-only",
            "original_manifest_sha256": digest(artifact / MANIFEST),
        },
    )
    product = manifest["products"][selection]
    product["version"] += ".installer-test.1"
    product["files"]["share/manager-suite/installer-fixture.json"] = {
        **object_file(marker, candidate),
        "executable": False,
    }
    product["release"] = product_hash(product)
    manifest["version"] = product["version"]
    seal(candidate, manifest)
    validate(candidate)
    return candidate


def installed_prefix(python, runner):
    # Isolated Windows Python ignores PYTHONUTF8; JSON keeps pipe output ASCII.
    return json.loads(
        runner(
            python, "-I", "-B", "-c", "import json,sys; print(json.dumps(sys.prefix))"
        ).stdout
    )


def qualify(artifact, individuals, report, bootstrap=None):
    artifact, manifest = validate(artifact.resolve())
    individuals = individuals.resolve()
    individual_manifests = {}
    for name in COMMANDS:
        path, item = validate((individuals / name).resolve())
        selected = "manager-suite/" + name
        assert item["selection"] == selected and set(item["products"]) == {selected}
        assert (
            item["products"][selected]["release"]
            == manifest["products"][selected]["release"]
        ), name
        individual_manifests[name] = digest(path / MANIFEST)
    with tempfile.TemporaryDirectory(
        prefix="mgr-", dir=None if os.name == "nt" else "/tmp"
    ) as temporary:
        base = Path(temporary).resolve()
        helper = base / "helper"
        shutil.copytree(
            ROOT / "scripts/dev",
            helper,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(
                (
                    "AGENT_",
                    "UNDERCURRENT_",
                    "UC_",
                    "ORCHESTRATION_",
                    "MCP_",
                    "PROMPT_",
                    "SKILL_",
                    "SERVER_",
                    "CARRIER_",
                    "RUNNER_",
                    "WORKFLOW_",
                )
            )
        }
        env.update(
            {
                "PATH": ""
                if os.name != "nt"
                else str(Path(os.environ["SystemRoot"]) / "System32"),
                "PIP_NO_INDEX": "1",
                "UV_OFFLINE": "1",
                "HTTP_PROXY": "http://127.0.0.1:1",
                "HTTPS_PROXY": "http://127.0.0.1:1",
                "PYTHONHOME": str(base / "no-python"),
                "PYTHONPATH": str(base / "no-source"),
                "HOME": str(base / "home"),
                "USERPROFILE": str(base / "home"),
                "XDG_CONFIG_HOME": str(base / "config"),
                "XDG_STATE_HOME": str(base / "state"),
            }
        )

        def run(*argv, ok=True, environment=None):
            result = subprocess.run(
                list(map(str, argv)),
                cwd=base,
                env=environment or env,
                capture_output=True,
                text=True,
                timeout=600,
            )
            if ok != (result.returncode == 0):
                raise RuntimeError(
                    f"Unexpected result ({result.returncode}): {argv}\n{result.stdout[-3000:]}\n{result.stderr[-5000:]}"
                )
            return result

        def consumer(action, *args, ok=True):
            result = run(
                sys.executable, "-I", "-B", helper / "consumer.py", action, *args, ok=ok
            )
            if not ok:
                return result
            value = (
                json.loads(result.stdout.splitlines()[-1])
                if "--events" in args
                else json.loads(result.stdout)
            )
            return value["result"] if "--events" in args else value

        def entries(store):
            consumer("inspect", "--prefix", store)
            return json.loads(
                (store / "installation-receipt.json").read_text(encoding="utf-8")
            )["products"]

        def command(store, name):
            return store / "bin" / (name + (".exe" if os.name == "nt" else ""))

        shared, private = base / "shared tools 世界", base / "suite tools 世界"
        # Install Agent first: its API copies must not create other public tools.
        order = [
            "agent-manager",
            *[name for name in COMMANDS if name != "agent-manager"],
        ]
        for name in order:
            options = (
                "--artifact",
                individuals / name,
                "--prefix",
                shared,
                "--request-id",
                "individual-" + name,
                "--events",
                "jsonl",
            )
            consumer("install", *options)
            consumer("install", *options)
            if name == "agent-manager":
                assert set(entries(shared)) == {"manager-suite/agent-manager"}
                for other in (
                    "prompt-manager",
                    "skill-manager",
                    "mcp-manager",
                    "conversation-manager",
                ):
                    assert not command(shared, other).exists(), other
        before = entries(shared)
        options = (
            "--artifact",
            artifact,
            "--prefix",
            shared,
            "--request-id",
            "install-all",
            "--events",
            "jsonl",
        )
        consumer("install", *options)
        consumer("install", *options)
        assert {k: v["payload"] for k, v in entries(shared).items()} == {
            k: v["payload"] for k, v in before.items()
        }
        consumer(
            "install",
            "--artifact",
            artifact,
            "--prefix",
            private,
            "--scope",
            "private",
            "--owner",
            "example-app",
        )
        consumer(
            "install",
            "--artifact",
            artifact,
            "--prefix",
            private,
            "--scope",
            "private",
            "--owner",
            "different-app",
            ok=False,
        )
        for store in (shared, private):
            installed = entries(store)
            assert set(installed) == set(manifest["products"])
            prefixes = []
            for item in installed.values():
                observed = installed_prefix(item["python"], run)
                assert Path(observed).resolve().is_relative_to(store)
                prefixes.append(observed)
                assert str(store / "runtimes") in (
                    store
                    / "bin"
                    / (
                        next(iter(item["product"]["commands"]))
                        + (".aixand-command" if os.name == "nt" else "")
                    )
                ).read_text(encoding="utf-8")
            assert len(set(prefixes)) == len(manifest["products"])

        # Real immutable prompt data persists through software replacement/removal.
        pack = base / "pack"
        pack.mkdir()
        (pack / "body.md").write_text("Persistent shared prompt.")
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
        shared_home, private_home = base / "shared-data", base / "private-data"
        for store, home, text in (
            (shared, shared_home, "Persistent shared prompt."),
            (private, private_home, "Private Suite prompt."),
        ):
            (pack / "body.md").write_text(text)
            run(
                command(store, "prompt-manager"),
                "--home",
                home,
                "release",
                pack,
                "--version",
                "1",
            )
            rendered = run(
                command(store, "prompt-manager"),
                "--home",
                home,
                "render",
                "retained@1/main",
            ).stdout
            assert text in rendered
        preserved = {
            p.relative_to(shared_home).as_posix(): digest(p)
            for p in shared_home.rglob("*")
            if p.is_file()
        }
        assert preserved
        prompt_id = "manager-suite/prompt-manager"
        old_payload = entries(shared)[prompt_id]["payload"]
        consumer(
            "reinstall",
            "--artifact",
            individuals / "prompt-manager",
            "--prefix",
            shared,
        )
        assert entries(shared)[prompt_id]["payload"] != old_payload
        consumer("rollback", "--prefix", shared, "--product", prompt_id, ok=False)
        damaged = base / "damaged"
        shutil.copytree(individuals / "prompt-manager", damaged)
        _, damaged_manifest = validate(damaged)
        (damaged / next(iter(damaged_manifest["files"]))).write_bytes(b"damaged")
        before_failed = entries(shared)[prompt_id]["payload"]
        consumer("upgrade", "--artifact", damaged, "--prefix", shared, ok=False)
        assert entries(shared)[prompt_id]["payload"] == before_failed

        # An aggregate owns only its implicit selections; these were all explicit.
        consumer(
            "uninstall", "--prefix", shared, "--product", "manager-suite", "--stopped"
        )
        assert set(entries(shared)) == set(manifest["products"])
        backup = base / "backup"
        shutil.copytree(shared_home, backup)
        assert all(digest(backup / name) == value for name, value in preserved.items())
        candidate = upgrade_fixture((individuals / "prompt-manager").resolve(), base)
        consumer("upgrade", "--artifact", candidate, "--prefix", shared)
        assert entries(shared)[prompt_id]["product"]["version"].endswith(
            ".installer-test.1"
        )
        assert (
            "Persistent shared prompt."
            in run(
                command(shared, "prompt-manager"),
                "--home",
                shared_home,
                "render",
                "retained@1/main",
            ).stdout
        )
        consumer(
            "uninstall", "--prefix", private, "--product", "manager-suite", ok=False
        )
        consumer(
            "uninstall", "--prefix", private, "--product", "manager-suite", "--stopped"
        )
        assert not consumer("inspect", "--prefix", private)["products"]
        shutil.rmtree(private)
        assert (
            "Persistent shared prompt."
            in run(
                command(shared, "prompt-manager"),
                "--home",
                shared_home,
                "render",
                "retained@1/main",
            ).stdout
        )
        for name in COMMANDS:
            consumer(
                "uninstall",
                "--prefix",
                shared,
                "--product",
                "manager-suite/" + name,
                "--stopped",
            )
        assert not consumer("inspect", "--prefix", shared)["products"]
        assert all(
            digest(shared_home / name) == value for name, value in preserved.items()
        )

        bootstrap_tested = False
        if bootstrap:
            bootstrap_env = {
                **env,
                "AIXAND_INSTALLER_CACHE": str(base / "bootstrap-cache"),
            }
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "PYTHONHOME", "PYTHONPATH"):
                bootstrap_env.pop(key, None)
            if os.name == "nt":
                launcher = [
                    shutil.which("pwsh") or shutil.which("powershell"),
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(bootstrap.resolve() / "bootstrap.ps1"),
                ]
            else:
                tools = base / "bootstrap-tools"
                tools.mkdir()
                for name in (
                    "sh",
                    "curl",
                    "tar",
                    "gzip",
                    "sha256sum",
                    "shasum",
                    "uname",
                    "mkdir",
                    "mktemp",
                    "mv",
                    "rm",
                    "rmdir",
                ):
                    if executable := shutil.which(name):
                        (tools / name).symlink_to(executable)
                bootstrap_env["PATH"] = str(tools)
                launcher = ["/bin/sh", str(bootstrap.resolve() / "bootstrap.sh")]
            store = base / "bootstrap-store"
            run(
                *launcher,
                "install",
                "--artifact",
                artifact,
                "--prefix",
                store,
                environment=bootstrap_env,
            )
            run(
                *launcher,
                "exec",
                "--prefix",
                store,
                "--command",
                "agent",
                "--",
                "--help",
                environment=bootstrap_env,
            )
            consumer(
                "uninstall",
                "--prefix",
                store,
                "--product",
                "manager-suite",
                "--stopped",
            )
            bootstrap_tested = True
        result = {
            "verified": True,
            "target": manifest["target"],
            "manifest_sha256": digest(artifact / MANIFEST),
            "individual_manifest_sha256": individual_manifests,
            "independent_products": len(manifest["products"]),
            "isolated_environments_per_aggregate": len(manifest["products"]),
            "api_dependencies_do_not_install_public_tools": True,
            "individual_aggregate_payload_reuse": True,
            "explicit_products_survive_collection_removal": True,
            "shared_and_private": True,
            "private_removal_preserves_shared": True,
            "offline_install_with_empty_tool_path": True,
            "request_resume": True,
            "owner_conflict_rejected": True,
            "reinstall": True,
            "corrupt_upgrade_rejected": True,
            "stateful_rollback_rejected": True,
            "upgrade_assembly_fixture_tested": True,
            "quiesced_backup_verified": True,
            "prompt_data_preserved": True,
            "stopped_uninstall": True,
            "bootstrap_without_system_python": bootstrap_tested,
        }
    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        with report.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--individuals", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--bootstrap", type=Path)
    args = parser.parse_args()
    qualify(args.artifact, args.individuals, args.report, args.bootstrap)
