"""Prepare a pinned native OpenCode executable for localhost integration tests."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/dev"))
from catalog import host

MAX_BYTES = 512 * 1024 * 1024


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not newurl.startswith("https://registry.npmjs.org/"):
            raise ValueError("Unexpected OpenCode fixture redirect")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def prepare(output, root=ROOT):
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("Choose a fresh OpenCode fixture output directory")
    pins = json.loads((root / ".github/ci/opencode-fixtures.json").read_text())
    policy = tomllib.loads((root / "provider-policy/manifest.toml").read_text())
    version = policy["providers"]["opencode"]["cli"]["install_version"]
    if pins.get("schema") != 1 or pins.get("version") != version:
        raise ValueError("Update OpenCode fixture pins to match the provider policy")
    target = host()
    record = pins["artifacts"][target]
    if not record["url"].startswith("https://registry.npmjs.org/"):
        raise ValueError("Unexpected OpenCode fixture source")
    algorithm, digest = record["integrity"].split("-", 1)
    if algorithm != "sha512":
        raise ValueError("OpenCode fixture pins must use SHA512 integrity")
    expected = base64.b64decode(digest, validate=True)
    with urllib.request.build_opener(HTTPSRedirect()).open(
        record["url"], timeout=60
    ) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES or not hmac.compare_digest(
        hashlib.sha512(data).digest(), expected
    ):
        raise ValueError("OpenCode fixture archive integrity mismatch")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        entries = [row for row in archive.getmembers() if row.name == record["member"]]
        if (
            len(entries) != 1
            or not entries[0].isfile()
            or not 0 < entries[0].size <= MAX_BYTES
        ):
            raise ValueError("OpenCode fixture must contain one regular executable")
        with archive.extractfile(entries[0]) as stream:
            executable = stream.read(MAX_BYTES + 1)
    if len(executable) > MAX_BYTES:
        raise ValueError("OpenCode fixture executable exceeds size limit")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".opencode-fixture-", dir=output.parent
    ) as temporary:
        staging = Path(temporary) / "payload"
        staging.mkdir()
        binary = staging / ("opencode.exe" if os.name == "nt" else "opencode")
        binary.write_bytes(executable)
        binary.chmod(0o755)
        result = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        if result.stdout.strip() not in (
            version,
            "v" + version,
            "opencode " + version,
            "opencode v" + version,
        ):
            raise ValueError("OpenCode fixture executable version differs from its pin")
        proof = {
            "version": version,
            "target": target,
            "archive_integrity": record["integrity"],
            "executable_sha256": hashlib.sha256(executable).hexdigest(),
        }
        (staging / "fixture.json").write_text(json.dumps(proof, indent=2) + "\n")
        staging.rename(output)
    return proof


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.output)))
