"""Validate release selection, independent versions, and committed source pins."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import subprocess
import tarfile
import tomllib
import zipfile

from ci_plan import PATHS, PRODUCTS, ROOT, consumers, output
from manager_profiles import PACKAGES
from qualify_release_archive import qualify as qualify_archive

LOCAL_SOURCES = {
    "libraries": ("../aixand-libraries", "AIXAND_LIBRARIES"),
    "undercurrent": ("../undercurrent", "AIXAND_UNDERCURRENT"),
}
VERSION = re.compile(
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(alpha|beta|rc)(?:\.(0|[1-9]\d*))?)?"
)


def native_matrix(product):
    targets = [
        {"os": "ubuntu-22.04", "target": "linux-x86_64-gnu"},
        {"os": "ubuntu-22.04-arm", "target": "linux-arm64-gnu"},
        {"os": "macos-15-intel", "target": "macos-x86_64"},
        {"os": "macos-15", "target": "macos-arm64"},
        {"os": "windows-2022", "target": "windows-x86_64-msvc"},
    ]
    # Agent's MCP dependencies no longer provide current Intel Mac wheels.
    # Conversation and the collection defer Intel installers for this beta too.
    if product in {"agent-manager", "conversation-manager", "manager-suite"}:
        targets = [target for target in targets if target["target"] != "macos-x86_64"]
    return {"include": targets}


def python_version(version):
    match = VERSION.fullmatch(version)
    if not match:
        raise ValueError(f"Unsupported release version: {version}")
    major, minor, patch, stage, number = match.groups()
    return f"{major}.{minor}.{patch}" + (
        {"alpha": "a", "beta": "b", "rc": "rc"}[stage] + (number or "0")
        if stage
        else ""
    )


def selection(product=None, tag=None, root=ROOT):
    manifest = tomllib.loads((root / "installation.toml").read_text())
    if tag:
        matched = [
            name
            for name in (*PRODUCTS, "manager-suite")
            if tag.startswith(
                (
                    "aixand-manager-suite"
                    if name == "manager-suite"
                    else PACKAGES[name][0]
                )
                + "/v"
            )
        ]
        if len(matched) != 1:
            raise ValueError("Unknown product release tag")
        product = matched[0]
    if product not in (*PRODUCTS, "manager-suite"):
        raise ValueError("Select a known product or manager-suite")
    record = (
        manifest["groups"]["manager-suite"]
        if product == "manager-suite"
        else manifest["products"][f"manager-suite/{product}"]
    )
    version = record["version"]
    python_version(version)
    expected = (
        ("aixand-manager-suite" if product == "manager-suite" else PACKAGES[product][0])
        + "/v"
        + version
    )
    if tag and tag != expected:
        raise ValueError(f"Tag must match the selected installer version: {expected}")
    return product, version, expected


def runtime_version(package, module):
    base = package / (module if (package / module).is_dir() else f"src/{module}")
    for path in (base / "_version.py", base / "__init__.py"):
        if path.exists():
            for node in ast.parse(path.read_text()).body:
                if isinstance(node, ast.Assign) and any(
                    isinstance(n, ast.Name) and n.id == "__version__"
                    for n in node.targets
                ):
                    return ast.literal_eval(node.value)
    raise ValueError(f"Missing static runtime version in {package}")


def validate_versions(product, root=ROOT):
    manifest = tomllib.loads((root / "installation.toml").read_text())
    groups = tomllib.loads((root / "installer/pyproject.toml").read_text())[
        "dependency-groups"
    ]
    selected = PRODUCTS if product == "manager-suite" else (product,)
    for name in selected:
        distribution, module = PACKAGES[name]
        package = (root / PATHS[name]).resolve()
        metadata = tomllib.loads((package / "pyproject.toml").read_text())["project"]
        expected = python_version(
            manifest["products"][f"manager-suite/{name}"]["version"]
        )
        if (
            metadata["name"] != distribution
            or metadata["version"] != expected
            or runtime_version(package, module) != expected
        ):
            raise ValueError(
                f"Align {name} package, runtime, and installer versions ({expected})"
            )
        if not any(
            re.fullmatch(
                re.escape(distribution) + r"(?:\[[^\]]+\])?==" + re.escape(expected),
                value,
            )
            for value in groups[name]
        ):
            raise ValueError(
                f"Update the {name} installer dependency pin to {expected}"
            )


def source_outputs(product, release=False, installer=False, root=ROOT, local=False):
    if release and local:
        raise ValueError(
            "Release qualification must use published external dependencies"
        )
    values = {}
    for name, (_, prefix) in LOCAL_SOURCES.items():
        repo = (
            os.environ.get(prefix + "_REPOSITORY")
            or {
                "libraries": "Aixand/aixand-libraries",
                "undercurrent": "Aixand/undercurrent",
            }[name]
        )
        needed = local and (
            name == "libraries"
            and product != "prompt-manager"
            or name == "undercurrent"
            and product in {"agent-manager", "conversation-manager"}
        )
        if needed and not repo:
            raise ValueError(f"Set {prefix}_REPOSITORY for {product} checks")
        values[name + "-needed"] = str(needed).lower()
        values[name + "-repository"] = repo
        values[name + "-ref"] = os.environ.get(prefix + "_REF") or "main"
    return values


def verify_sources(root=ROOT):
    if subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=root, text=True
    ).strip():
        raise ValueError("The release project checkout has uncommitted changes")
    return {}


def release_notes(product, version, root=ROOT):
    for path in (
        root / "docs/release-notes" / product / (version + ".md"),
        root / "docs/release-notes" / (version + ".md"),
        root / "docs/beta-release-notes.md",
    ):
        if path.is_file():
            with path.open(encoding="utf-8") as stream:
                if version in stream.readline().split():
                    return path
    raise ValueError(f"Add release notes identifying {product} version {version}")


def public_archive_member(member):
    """Keep local builder identities out of downloadable tar metadata."""
    member.uid = member.gid = 0
    member.uname = member.gname = ""
    member.pax_headers = {
        key: value
        for key, value in member.pax_headers.items()
        if key not in {"uid", "gid", "uname", "gname"}
    }
    return member


def archive(product, directory, destination, root=ROOT, target_expected=None):
    product, version, tag = selection(product, root=root)
    notes = release_notes(product, version, root)
    # Read the actual native target from the qualified exported descriptor.
    relative = (
        "handoff/manager-suite"
        if product == "manager-suite"
        else f"handoff/products/{product}"
    )
    handoff = directory / relative
    descriptor = json.loads((handoff / "product-descriptor.json").read_text())
    target = descriptor["target"]
    if target_expected and target != target_expected:
        raise ValueError("Runner architecture differs from the requested native target")
    if (
        descriptor["version"] != version
        or descriptor["selection"]
        != (
            "manager-suite"
            if product == "manager-suite"
            else f"manager-suite/{product}"
        )
        or not descriptor["verification"]["runtime"]
    ):
        raise ValueError(
            "Release export must match the selected version and installed verification"
        )
    lifecycle = json.loads((directory / "lifecycle.json").read_text())
    if (
        not lifecycle.get("passed", lifecycle.get("verified", False))
        or lifecycle.get("manifest_sha256") != descriptor["manifest_sha256"]
        or lifecycle.get("target") != target
    ):
        raise ValueError("Lifecycle report must match this qualified artifact")
    name = (
        "aixand-manager-suite" if product == "manager-suite" else PACKAGES[product][0]
    ) + f"-{version}-{target}"
    source_records = verify_sources(root)
    destination.mkdir(parents=True, exist_ok=False)
    extension = ".zip" if target.startswith("windows-") else ".tar.gz"
    path = destination / (name + extension)
    with tempfile.TemporaryDirectory(prefix="manager-release-archive-") as temporary:
        staging = Path(temporary) / name
        shutil.copytree(handoff, staging)
        shutil.copytree(directory / "bootstrap", staging / "bootstrap")
        guide = (ROOT / "docs/release-installation.md").read_text(encoding="utf-8")
        (staging / "INSTALL.md").write_text(
            guide.replace("(beta-release-notes.md)", "(RELEASE-NOTES.md)"),
            encoding="utf-8",
        )
        shutil.copyfile(notes, staging / "RELEASE-NOTES.md")
        reports = staging / "reports"
        reports.mkdir()
        for report in directory.glob("*.json"):
            shutil.copyfile(report, reports / report.name)
        if extension == ".zip":
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as bundle:
                for file in sorted(staging.rglob("*")):
                    if file.is_file():
                        bundle.write(
                            file, f"{name}/{file.relative_to(staging).as_posix()}"
                        )
        else:
            with tarfile.open(path, "w:gz") as bundle:
                bundle.add(staging, arcname=name, filter=public_archive_member)
    qualification = qualify_archive(path)
    qualification_path = destination / f"{name}-archive-qualification.json"
    qualification_path.write_text(json.dumps(qualification, indent=2) + "\n")
    identity = destination / f"{name}-payload-identities.json"
    identity.write_text(
        json.dumps(
            {"schema": 1, "target": target, "products": descriptor["products"]},
            indent=2,
        )
        + "\n"
    )
    provenance = {
        "tag": tag,
        "product": product,
        "version": version,
        "target": target,
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "sources": source_records,
        "dependency_artifacts_sha256": hashlib.sha256(
            (root / ".github/ci/dependencies.json").read_bytes()
        ).hexdigest(),
    }
    report = destination / f"{name}-provenance.json"
    report.write_text(json.dumps(provenance, indent=2) + "\n")
    (destination / f"{name}-SHA256SUMS.txt").write_text(
        "".join(
            f"{hashlib.sha256(file.read_bytes()).hexdigest()}  {file.name}\n"
            for file in (path, report, qualification_path, identity)
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["plan", "sources", "validate", "archive"])
    parser.add_argument("--product", choices=[*PRODUCTS, "manager-suite"])
    parser.add_argument("--tag")
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--installer", action="store_true")
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--github-output")
    parser.add_argument("--target")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.operation == "sources":
            values = source_outputs(
                args.product, args.release, args.installer, local=args.local
            )
        else:
            product, version, tag = selection(args.product, args.tag)
            if args.operation == "plan":
                selected = (
                    sorted(PRODUCTS)
                    if product == "manager-suite"
                    else consumers([product])
                )
                values = {
                    "product": product,
                    "native-matrix": native_matrix(product),
                    "native-count": len(native_matrix(product)["include"]),
                    "version": version,
                    "tag": tag,
                    "prerelease": str("-" in version).lower(),
                    "matrix": {
                        "include": [
                            {"product": name, "python": interpreter}
                            for name in selected
                            for interpreter in ["3.11", "3.14"]
                        ]
                    },
                }
            elif args.operation == "validate":
                validate_versions(product)
                values = {}
                if args.release:
                    values = {"sources": verify_sources()}
            else:
                if not args.input or not args.output:
                    raise ValueError("archive requires --input and --output")
                archive(product, args.input, args.output, target_expected=args.target)
                values = {}
        output(values, args.github_output)
        print(json.dumps(values))
    except (ValueError, KeyError) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
