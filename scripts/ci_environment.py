"""Prepare CI with published external wheels and current in-suite dependency wheels."""

from __future__ import annotations

from email.parser import BytesParser
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib
import zipfile

from ci_plan import PATHS, PRODUCTS
from manager_profiles import PROFILES
from published_dependencies import fetch, manifest
from source_snapshot import source_snapshot


def run(*arguments, cwd):
    subprocess.run(list(map(str, arguments)), cwd=cwd, check=True)


def blocks(text):
    result = []
    for line in text.splitlines(keepends=True):
        if line.strip() and not line.startswith((" ", "\t", "#")):
            result.append(line)
        elif result:
            result[-1] += line
    return result


def requirement_name(block):
    return re.split(r"[=<>!~;\s\[]", block, maxsplit=1)[0]


def wheel_requirement(block, name, version, path):
    first = block.splitlines()[0]
    if not re.fullmatch(re.escape(name) + r"==" + re.escape(version), first):
        raise ValueError(f"Regenerate the development lock for {name} {version}")
    return f"{name} @ {path.as_uri()}#sha256={hashlib.sha256(path.read_bytes()).hexdigest()}\n"


def develop(product, version, root, output):
    package = (root / PATHS[product]).resolve()
    config = tomllib.loads((package / "development.toml").read_text())
    python = (
        package / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    if not python.exists():
        run("uv", "venv", package / ".venv", "--python", version, cwd=package)
    published = manifest(root)
    requirements = blocks((package / "requirements-dev.lock").read_text())
    names = {requirement_name(block) for block in requirements}
    with tempfile.TemporaryDirectory(prefix="manager-ci-inputs-") as temporary:
        wheelhouse = Path(temporary) / "wheels"
        wheelhouse.mkdir()
        replacements = {}
        # Only unpublished in-suite dependencies are built. External sources are never consulted.
        for dependency in PROFILES[product]:
            if dependency == product or dependency not in PRODUCTS:
                continue
            source = (root / PATHS[dependency]).resolve()
            metadata = tomllib.loads((source / "pyproject.toml").read_text())["project"]
            if metadata["name"] not in names:
                continue
            with source_snapshot(source, output) as snapshot:
                run(
                    "uv",
                    "build",
                    "--wheel",
                    "--no-sources",
                    "--no-build-logs",
                    "--python",
                    version,
                    "--out-dir",
                    wheelhouse,
                    snapshot,
                    cwd=package,
                )
            for wheel in wheelhouse.glob("*.whl"):
                with zipfile.ZipFile(wheel) as archive:
                    info = BytesParser().parsebytes(
                        archive.read(
                            next(
                                p
                                for p in archive.namelist()
                                if p.endswith(".dist-info/METADATA")
                            )
                        )
                    )
                if info["Name"] == metadata["name"]:
                    replacements[metadata["name"]] = (metadata["version"], wheel)
                    break
            else:
                raise ValueError(f"Missing built dependency wheel: {dependency}")
        for name in names.intersection(published):
            wheel = fetch(name, root)
            replacements[name] = (published[name]["version"], wheel)
        selected = Path(temporary) / "requirements.lock"
        selected.write_text(
            "".join(
                wheel_requirement(block, name, *replacements[name])
                if (name := requirement_name(block)) in replacements
                else block
                for block in requirements
            )
        )
        run(
            "uv",
            "pip",
            "sync",
            "--python",
            python,
            "--no-sources",
            selected,
            cwd=package,
        )
        extras = ",".join(config["development"].get("extras", []))
        own = str(package) + (f"[{extras}]" if extras else "")
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
            own,
            cwd=package,
        )
        if product in {"agent-manager", "conversation-manager"}:
            run(
                "uv",
                "pip",
                "install",
                "--python",
                python,
                "--no-deps",
                fetch("aixand-undercurrent-sdk", root),
                cwd=package,
            )
        run("uv", "pip", "check", "--python", python, cwd=package)


if __name__ == "__main__":
    import argparse
    from ci_plan import ROOT

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("product", choices=PRODUCTS)
    parser.add_argument("--python", default="3.12.14")
    args = parser.parse_args()
    develop(args.product, args.python, ROOT, ROOT / "build/ci-inputs")
