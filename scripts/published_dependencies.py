"""Fetch pinned published Python wheels without requiring sibling source trees."""

from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MAX_WHEEL = 32 * 1024 * 1024


def manifest(root=ROOT):
    data = json.loads((root / ".github/ci/dependencies.json").read_text())
    if data.get("schema") != 1 or not isinstance(data.get("packages"), dict):
        raise ValueError("Unsupported published dependency manifest")
    return data["packages"]


def sha(content):
    return hashlib.sha256(content).hexdigest()


def download(url, expected):
    if urllib.parse.urlsplit(url).scheme != "https" or not re.fullmatch(
        r"[0-9a-f]{64}", expected
    ):
        raise ValueError("Published artifacts need HTTPS and a SHA-256 pin")
    request = urllib.request.Request(
        url, headers={"User-Agent": "aixand-manager-dependencies"}
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        content = response.read(MAX_WHEEL + 1)
    if len(content) > MAX_WHEEL or sha(content) != expected:
        raise ValueError("Published dependency size or checksum differs")
    return content


def verify_wheel(content, name, record):
    wheel = record["wheel"]
    if sha(content) != wheel["sha256"]:
        raise ValueError(f"Published wheel checksum differs: {name}")
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        metadata_paths = [
            p for p in archive.namelist() if p.endswith(".dist-info/METADATA")
        ]
        if len(metadata_paths) != 1:
            raise ValueError("Expected one wheel metadata record")
        metadata = BytesParser().parsebytes(archive.read(metadata_paths[0]))
        if (
            metadata["Name"].replace("_", "-").lower() != name
            or metadata["Version"] != record["version"]
        ):
            raise ValueError(f"Published wheel identity differs: {name}")
    if not wheel["filename"].endswith("-py3-none-any.whl"):
        raise ValueError("This dependency cache accepts universal Python wheels only")


def fetch(name, root=ROOT):
    record = manifest(root)[name]
    filename = record["wheel"]["filename"]
    if Path(filename).name != filename or not filename.endswith(".whl"):
        raise ValueError("Invalid published wheel filename")
    cache = root / "build/published-wheels"
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / filename
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink():
            raise ValueError("Published wheel cache cannot contain symlinks")
        verify_wheel(destination.read_bytes(), name, record)
        return destination
    content = download(record["wheel"]["url"], record["wheel"]["sha256"])
    verify_wheel(content, name, record)
    with tempfile.NamedTemporaryFile(
        dir=cache, prefix=".download-", delete=False
    ) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packages", nargs="*", default=["aixand-undercurrent-sdk"])
    args = parser.parse_args()
    for package in args.packages:
        print(fetch(package))
