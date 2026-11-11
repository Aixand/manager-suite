"""Check a package independently and inspect its installed wheel outside the source."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib

from ci_plan import PATHS, PRODUCTS, ROOT
from manager_profiles import COMMANDS, PACKAGES

INSTALLED_CHECK = """
import importlib, importlib.metadata, pathlib, sys
module = importlib.import_module(sys.argv[1])
assert not pathlib.Path(module.__file__).resolve().is_relative_to(pathlib.Path(sys.argv[2]).resolve())
assert importlib.metadata.version(sys.argv[3]) == sys.argv[4]
"""


def matches_python(actual, requested):
    return actual.split(".")[: len(requested.split("."))] == requested.split(".")


def run(*command, cwd=ROOT):
    environment = dict(os.environ)
    for key in (
        "UC_TEST_BIN",
        "CODEX_TEST_BIN",
        "OPENCODE_LIVE",
        "AGENT_MANAGER_LIVE",
        "CONVERSATION_MANAGER_LIVE",
    ):
        environment.pop(key, None)
    subprocess.run(list(map(str, command)), cwd=cwd, env=environment, check=True)


def check(product, version, output, local=False):
    package = (ROOT / PATHS[product]).resolve()
    metadata = tomllib.loads((package / "pyproject.toml").read_text())["project"]
    if metadata["name"] != PACKAGES[product][0]:
        raise ValueError("Distribution identity differs from release profile")
    if (
        metadata["license"] != "Apache-2.0"
        or (package / "LICENSE").read_bytes() != (ROOT / "LICENSE").read_bytes()
    ):
        raise ValueError("Package license differs from the suite")
    helper = package / "dev.py"
    if local:
        run(
            sys.executable,
            "-B",
            helper,
            "develop",
            "--local",
            "--python",
            version,
            cwd=package,
        )
        if product in {"agent-manager", "conversation-manager"}:
            local_python = (
                package
                / ".venv"
                / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            )
            run(
                "uv",
                "pip",
                "install",
                "--python",
                local_python,
                "--no-deps",
                "--no-sources",
                "--editable",
                ROOT.parent / "undercurrent/python",
                cwd=package,
            )
    else:
        from ci_environment import develop

        develop(product, version, ROOT, output)
    python = (
        package / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    actual = subprocess.check_output(
        [
            str(python),
            "-I",
            "-c",
            "import sys; print('.'.join(map(str, sys.version_info[:3])))",
        ],
        text=True,
    ).strip()
    if not matches_python(actual, version):
        raise ValueError(
            f"Package environment uses Python {actual}; requested {version}. Use a fresh checkout or recreate that package environment."
        )
    run(sys.executable, "-B", helper, "check", cwd=package)
    run(sys.executable, "-B", helper, "test", cwd=package)
    run(
        sys.executable,
        "-B",
        helper,
        "build",
        "--python",
        version,
        "--output",
        output,
        cwd=package,
    )
    wheels = list(Path(output).glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError("Expected one package wheel")
    python = (
        package / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    run(
        "uv",
        "pip",
        "install",
        "--python",
        python,
        "--no-deps",
        "--force-reinstall",
        wheels[0],
    )
    distribution, module = PACKAGES[product]
    try:
        with tempfile.TemporaryDirectory(prefix="manager-wheel-consumer-") as temporary:
            run(
                python,
                "-I",
                "-c",
                INSTALLED_CHECK,
                module,
                package / module
                if (package / module).is_dir()
                else package / "src" / module,
                distribution,
                metadata["version"],
                cwd=temporary,
            )
            for command in COMMANDS[product]:
                entry = python.parent / (command + (".exe" if os.name == "nt" else ""))
                run(entry, "--help", cwd=temporary)
    finally:
        run(
            "uv",
            "pip",
            "install",
            "--python",
            python,
            "--no-deps",
            "--no-sources",
            "--no-build-isolation",
            "--editable",
            package,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("product", choices=PRODUCTS)
    parser.add_argument("--python", default="3.11")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--local",
        action="store_true",
        help="Explicit source integration with sibling checkouts",
    )
    args = parser.parse_args()
    check(args.product, args.python, args.output.resolve(), args.local)


if __name__ == "__main__":
    main()
