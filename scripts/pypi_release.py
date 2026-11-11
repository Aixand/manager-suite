"""Validate release distributions and select missing PyPI files for safe retries."""

import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import re
import shutil
import tarfile
import tomllib
from urllib.error import HTTPError
from urllib.request import urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PRODUCTS = {
    "agent-manager",
    "conversation-manager",
    "mcp-manager",
    "prompt-manager",
    "skill-manager",
}


def normalized(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def distributions(product, directory):
    if product not in PRODUCTS:
        raise ValueError(f"Unknown PyPI product: {product}")
    project = tomllib.loads((ROOT / product / "pyproject.toml").read_text())["project"]
    files = sorted(directory.iterdir())
    wheels = [path for path in files if path.name.endswith(".whl")]
    sources = [path for path in files if path.name.endswith(".tar.gz")]
    if len(files) != 2 or len(wheels) != 1 or len(sources) != 1:
        raise ValueError("Expected exactly one wheel and one source distribution")
    with zipfile.ZipFile(wheels[0]) as archive:
        metadata = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata) != 1:
            raise ValueError("Expected one wheel metadata file")
        wheel_metadata = archive.read(metadata[0])
    with tarfile.open(sources[0]) as archive:
        metadata = [
            member
            for member in archive.getmembers()
            if member.name.count("/") == 1 and member.name.endswith("/PKG-INFO")
        ]
        if len(metadata) != 1:
            raise ValueError("Expected one source metadata file")
        source_metadata = archive.extractfile(metadata[0]).read()
    for raw in (wheel_metadata, source_metadata):
        metadata = BytesParser().parsebytes(raw)
        if (
            normalized(metadata["Name"]) != project["name"]
            or metadata["Version"] != project["version"]
        ):
            raise ValueError(
                "Distribution identity does not match selected product/version"
            )
    return files, project


def select_missing(files, published):
    local = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    remote = {item["filename"]: item["digests"]["sha256"] for item in published}
    for name, digest in remote.items():
        if local.get(name) != digest:
            raise ValueError(
                f"Existing PyPI file differs from qualified release: {name}"
            )
    return [path for path in files if path.name not in remote]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("product", choices=sorted(PRODUCTS))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--pending", required=True, type=Path)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    files, project = distributions(args.product, args.directory)
    url = f"https://pypi.org/pypi/{project['name']}/{project['version']}/json"
    try:
        with urlopen(url, timeout=30) as response:
            published = json.load(response)["urls"]
    except HTTPError as error:
        if error.code != 404:
            raise
        published = []
    missing = select_missing(files, published)
    args.pending.mkdir(parents=True, exist_ok=False)
    for path in missing:
        shutil.copy2(path, args.pending / path.name)
    print(
        f"Validated {project['name']} {project['version']}; {len(missing)} files to publish"
    )
    if args.github_output:
        with args.github_output.open("a") as output:
            output.write(f"count={len(missing)}\n")


if __name__ == "__main__":
    main()
