"""Locked local development and multi-wheel packaging; never publishes."""

from __future__ import annotations

import argparse
import os
import json
import re
import shutil
from pathlib import Path
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = (
    "mcp-manager",
    "prompt-manager",
    "skill-manager",
    "conversation-manager",
    "agent-manager",
)
LIVE = (
    "UC_TEST_BIN",
    "CODEX_TEST_BIN",
    "OPENCODE_LIVE",
    "AGENT_MANAGER_LIVE",
)


def run(argv, *, cwd=ROOT, env=None):
    print("+ " + " ".join(map(str, argv)), file=sys.stderr)
    subprocess.run(list(map(str, argv)), cwd=cwd, env=env, check=True)


def interpreter():
    environment = Path(os.environ.get("UV_PROJECT_ENVIRONMENT", ROOT / ".venv"))
    path = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not path.is_file():
        raise RuntimeError("Run ./dev.sh develop first")
    return path


def develop(*, sdk_backends=False):
    if not (ROOT / "uv.lock").is_file():
        raise RuntimeError(
            "Missing uv.lock; create/update it intentionally with uv lock"
        )
    request = os.environ.get(
        "UV_PYTHON", (ROOT / ".python-version").read_text().strip()
    )
    run(
        [
            "uv",
            "sync",
            "--locked",
            "--python",
            request,
            *(["--group", "sdk-backends"] if sdk_backends else []),
        ]
    )
    # All local components are installed in one transaction. Registry resolution of
    # unpublished siblings and optional private SDKs is intentionally avoided.
    run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            interpreter(),
            "--no-deps",
            "--no-build-isolation",
            *[arg for name in COMPONENTS for arg in ["--editable", ROOT / name]],
        ]
    )
    run(["uv", "pip", "check", "--python", interpreter()])


def license_check():
    reference = (ROOT / "LICENSE").read_bytes()
    for component in COMPONENTS:
        path = ROOT / component
        data = tomllib.loads((path / "pyproject.toml").read_text())
        assert data["project"]["license"] == "Apache-2.0", component
        assert data["project"]["license-files"] == ["LICENSE"], component
        assert (path / "LICENSE").read_bytes() == reference, component
        assert all(
            re.fullmatch(r"[A-Za-z0-9_-]+==[0-9.]+", requirement)
            for requirement in data["build-system"]["requires"]
        ), component
    print("All component license files and metadata agree")


def check():
    python = interpreter()
    run([python, "-B", "scripts/checks/check-repository-files.py"])
    run([python, "-m", "ruff", "check", "--config", "pyproject.toml", "."])
    run(
        [
            python,
            "-m",
            "ruff",
            "format",
            "--check",
            "scripts/dev",
            "scripts/development",
            "scripts/suite.py",
            "scripts/manager_dev.py",
            "scripts/manager_profiles.py",
            "scripts/prepare_runtime.py",
            "scripts/product_payload.py",
            "scripts/source_snapshot.py",
            "scripts/verify_product.py",
            "scripts/qualify_product_installation.py",
            "scripts/build_installers.py",
            "scripts/ci_plan.py",
            "scripts/ci_product.py",
            "scripts/ci_environment.py",
            "scripts/published_dependencies.py",
            "scripts/ci_release.py",
            "scripts/qualify_single_product.py",
        ]
    )
    run([python, "-B", "tests/tooling/checks/engine_dependencies_test.py"])
    run([python, "-B", "tests/tooling/checks/source_dependencies_test.py"])
    run([python, "-B", "tests/tooling/checks/development_entrypoints_test.py"])
    run([python, "-B", "tests/tooling/checks/docs_test.py"])
    run([python, "-B", "tests/tooling/checks/current_wording_test.py"])
    run([python, "-B", "tests/tooling/checks/distribution_names_test.py"])
    run([python, "-B", "tests/tooling/checks/installer_routing_test.py"])
    run([python, "-B", "tests/tooling/checks/ci_test.py"])
    run([python, "-B", "tests/tooling/checks/published_dependencies_test.py"])
    run([python, "-B", "scripts/checks/check-docs.py", "--root", "."])
    license_check()
    result = subprocess.check_output(
        ["git", "ls-files", "--stage"], cwd=ROOT, text=True
    )
    assert not any(line.startswith("160000 ") for line in result.splitlines()), (
        "Nested Git repository"
    )
    run(["git", "diff", "--check"])
    run(["git", "diff", "--cached", "--check"])


def test():
    env = dict(os.environ)
    for key in (*LIVE, "PYTHONPATH", "PYTHONHOME"):
        env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for component in COMPONENTS:
        run(
            [interpreter(), "-B", "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=ROOT / component,
            env=env,
        )


def build(output):
    from provider_policy import synchronize

    synchronize(check=True)
    output = output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    sources = [ROOT / name for name in COMPONENTS]
    for source in sources:
        from source_snapshot import source_snapshot

        with source_snapshot(source, output) as snapshot:
            run(
                [
                    "uv",
                    "build",
                    "--no-sources",
                    "--no-build-logs",
                    "--python",
                    interpreter(),
                    "--out-dir",
                    output,
                    snapshot,
                ]
            )
    return output


def doctor():
    from manager_profiles import PYTHON

    tools = {
        name: shutil.which(name)
        for name in (["uv", "rustup"] if os.name == "nt" else ["uv"])
    }
    checks = {"source:carrier": (ROOT / "../carrier/pyproject.toml").is_file()}
    for name in COMPONENTS:
        checks["source:" + name] = (ROOT / name / "pyproject.toml").is_file()
    checks["source:undercurrent"] = (
        ROOT / "../undercurrent/python/pyproject.toml"
    ).is_file()
    if tools.get("uv"):
        checks["python:" + PYTHON] = (
            subprocess.run(
                [tools["uv"], "python", "find", PYTHON, "--no-python-downloads"],
                cwd=ROOT,
                capture_output=True,
            ).returncode
            == 0
        )
    else:
        checks["python:" + PYTHON] = False
    if os.name == "nt":
        checks["launcher-toolchain"] = (
            bool(tools.get("rustup"))
            and subprocess.run(
                [tools["rustup"], "run", "1.98.0", "rustc", "--version"],
                capture_output=True,
            ).returncode
            == 0
        )
    from provider_policy import synchronize

    try:
        synchronize(check=True)
        checks["provider-policy"] = True
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        checks["provider-policy"] = False
        checks["provider-policy-error"] = str(error)
    return {
        "ready": all(tools.values())
        and all(
            value is True for key, value in checks.items() if not key.endswith("-error")
        ),
        "tools": tools,
        "checks": checks,
        "mutation_performed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=["doctor", "develop", "check", "test", "build", "licenses"],
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--sdk-backends",
        action="store_true",
        default=os.environ.get("AIXAND_SDK_BACKENDS") == "1",
        help="Install the pinned optional Claude, Antigravity and Copilot SDKs",
    )
    args = parser.parse_args()
    if args.operation == "doctor":
        result = doctor()
        print(json.dumps(result, indent=2))
        return
    if args.operation == "build":
        if not args.output:
            parser.error("--output is required")
        build(args.output)
    else:
        if args.operation == "develop":
            develop(sdk_backends=args.sdk_backends)
        else:
            globals()[
                {"licenses": "license_check"}.get(args.operation, args.operation)
            ]()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
