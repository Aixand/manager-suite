"""Build reproducible first-party wheels and exact native dependency wheelhouses."""

from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import urllib.parse
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/dev"))
from catalog import host
from core import digest
from runtime_support import download
from manager_profiles import (
    COMMANDS,
    EXTRAS,
    MANAGED_PACKAGES,
    PACKAGES,
    PROFILES,
    PYTHON,
)
from prepare_runtime import prepare


def run(*argv, capture=False):
    return subprocess.run(
        list(map(str, argv)),
        cwd=ROOT,
        check=True,
        text=True,
        env=dict(
            os.environ, SOURCE_DATE_EPOCH="315532800", PYTHONDONTWRITEBYTECODE="1"
        ),
        stdout=subprocess.PIPE if capture else sys.stderr,
    ).stdout


def notices(output):
    inventory = []
    for path in sorted((output / "wheelhouse").glob("*.whl")):
        with zipfile.ZipFile(path) as wheel:
            metadata = next(
                n for n in wheel.namelist() if n.endswith(".dist-info/METADATA")
            )
            info = BytesParser().parsebytes(wheel.read(metadata))
            entries = [
                n
                for n in wheel.namelist()
                if not n.endswith("/")
                and ".dist-info/" in n
                and (
                    n == metadata
                    or any(v in n.lower() for v in ("license", "notice", "copying"))
                )
            ]
            from dependency_notices import supplemental_notices

            supplements = (
                supplemental_notices(info["Name"], info["Version"], path, output)
                if not any(n != metadata for n in entries)
                else []
            )
            if not any(n != metadata for n in entries) and not supplements:
                raise RuntimeError(f"Missing distributed license notice: {path.name}")
            notice_files = {}
            for name in entries:
                content = wheel.read(name)
                logical = (
                    "share/notices/wheels/"
                    + hashlib.sha256(content).hexdigest()
                    + ".txt"
                )
                dest = output / logical
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(content)
                notice_files[name] = logical
            inventory.append(
                {
                    "name": info["Name"],
                    "version": info["Version"],
                    "wheel": path.name,
                    "sha256": digest(path),
                    "license_expression": info.get("License-Expression"),
                    "license": info.get("License"),
                    "notices": entries,
                    "notice_files": notice_files,
                    "supplemental_notices": supplements,
                }
            )
    return inventory


def wheel(output, source, dependency_wheelhouses=()):
    from provider_policy import synchronize

    synchronize(check=True)
    from source_snapshot import source_snapshot

    with source_snapshot(source, output) as snapshot:
        run(
            "uv",
            "build",
            "--wheel",
            "--no-sources",
            "--python",
            PYTHON,
            "--build-constraints",
            ROOT / "installer/build-constraints.txt",
            "--out-dir",
            output / "wheelhouse",
            snapshot,
        )
    for directory in dependency_wheelhouses:
        wheels = sorted(directory.glob("*.whl"))
        if not wheels:
            raise RuntimeError(f"Dependency wheelhouse is empty: {directory}")
        for dependency in wheels:
            destination = output / "wheelhouse" / dependency.name
            if destination.exists():
                raise RuntimeError(f"Duplicate dependency wheel: {dependency.name}")
            shutil.copyfile(dependency, destination)
    inventory = notices(output)
    for item in inventory:
        dest = output / "share/notices/inventory" / (item["wheel"] + ".json")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(item, indent=2, sort_keys=True) + "\n")


def dependencies(output, target, profile):
    prepare(target)
    wheelhouse = output / "wheelhouse"
    wheelhouse.mkdir(parents=True)
    export = run(
        "uv",
        "export",
        "--project",
        ROOT / "installer",
        "--locked",
        "--only-group",
        profile,
        "--no-emit-project",
        *[
            arg
            for name in sorted(MANAGED_PACKAGES)
            for arg in ("--no-emit-package", name)
        ],
        "--format",
        "requirements.txt",
        "--no-hashes",
        "--no-header",
        "--no-annotate",
        capture=True,
    )
    lock = tomllib.loads((ROOT / "installer/uv.lock").read_text())
    packages = {(p["name"], p["version"]): p for p in lock["package"]}
    native = json.loads(
        run(
            "uv",
            "run",
            "--no-project",
            "--python",
            PYTHON,
            "--with",
            "pip==26.2",
            "python",
            "-I",
            "-c",
            "import json,sys; from pip._vendor.packaging.tags import sys_tags; "
            "from pip._vendor.packaging.requirements import Requirement; "
            "rs=[Requirement(v) for v in sys.argv[1].splitlines() if v.strip() and not v.startswith('#')]; "
            "print(json.dumps({'tags':list(map(str,sys_tags())), 'selected':[str(v).split(';')[0].strip() for v in rs if v.marker is None or v.marker.evaluate()]}))",
            export,
            capture=True,
        )
    )
    ranking = {tag: i for i, tag in enumerate(native["tags"])}
    locked = []
    cache = ROOT / "build/locked-wheels" / target
    cache.mkdir(parents=True, exist_ok=True)
    for spec in native["selected"]:
        name, version = spec.split("==")
        choices = []
        for record in packages[name, version].get("wheels", []):
            filename = urllib.parse.unquote(
                urllib.parse.urlsplit(record["url"]).path.rsplit("/", 1)[-1]
            )
            python, abi, platform = filename[:-4].rsplit("-", 3)[-3:]
            score = min(
                (
                    ranking.get(f"{p}-{a}-{s}", 10**9)
                    for p in python.split(".")
                    for a in abi.split(".")
                    for s in platform.split(".")
                ),
                default=10**9,
            )
            if score < 10**9:
                choices.append((score, filename, record))
        if not choices:
            raise RuntimeError(f"No locked native wheel for {spec} on {target}")
        _, filename, record = min(choices, key=lambda x: (x[0], x[1]))
        path = cache / filename
        expected = record["hash"].removeprefix("sha256:")
        if path.exists():
            if path.is_symlink() or digest(path) != expected:
                raise RuntimeError(f"Changed wheel cache; remove explicitly: {path}")
        else:
            download(record["url"], path, expected)
        shutil.copyfile(path, wheelhouse / filename)
        locked.append(
            {
                "name": name,
                "version": version,
                "wheel": filename,
                "sha256": expected,
                "source": record["url"],
            }
        )
    share = output / "share/manager-suite" / profile
    share.mkdir(parents=True)
    (share / "requirements.txt").write_text(export, encoding="utf-8")
    (share / "dependencies.json").write_text(
        json.dumps(locked, indent=2, sort_keys=True) + "\n"
    )
    (share / "inventory.json").write_text(
        json.dumps(notices(output), indent=2, sort_keys=True) + "\n"
    )
    (share / "profile.json").write_text(
        json.dumps(
            {
                "product": profile,
                "packages": {k: PACKAGES[k] for k in PROFILES[profile]},
                "all_packages": PACKAGES,
                "commands": COMMANDS[profile],
                "extras": EXTRAS.get(profile, []),
                "python": PYTHON,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    shutil.copyfile(ROOT / "installer/uv.lock", share / "uv.lock")
    shutil.copyfile(ROOT / "scripts/verify_product.py", share / "verify.py")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("wheel", "dependencies"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument(
        "--dependency-wheelhouse", type=Path, action="append", default=[]
    )
    parser.add_argument("--profile", choices=PROFILES)
    args = parser.parse_args()
    if args.target != host():
        parser.error("Build and test each wheelhouse on its native target")
    if args.operation == "wheel":
        if not args.source:
            parser.error("--source is required for wheel")
        wheel(args.output.resolve(), args.source.resolve(), args.dependency_wheelhouse)
    else:
        if not args.profile:
            parser.error("--profile is required for dependencies")
        dependencies(args.output.resolve(), args.target, args.profile)


if __name__ == "__main__":
    main()
