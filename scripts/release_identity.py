"""Reject different product payloads carrying the same version and native target."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re


def check(current, history):
    identities = {}
    for path in [
        *sorted(Path(history).glob("*.json")),
        *sorted(Path(current).glob("*-payload-identities.json")),
    ]:
        record = json.loads(path.read_text())
        if record.get("schema") != 1 or not record.get("products"):
            raise ValueError(f"Invalid release identity record: {path}")
        for name, product in record["products"].items():
            key = (name, product["version"], record["target"])
            release = product["release"]
            if not re.fullmatch(r"[0-9a-f]{64}", release):
                raise ValueError(f"Invalid product release hash: {path}")
            if key in identities and identities[key] != release:
                raise ValueError(
                    f"Changed payload for {name} {key[1]} on {key[2]}; bump the product version"
                )
            identities[key] = release
    if not list(Path(current).glob("*-payload-identities.json")):
        raise ValueError("No current release identities found")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--history", type=Path, required=True)
    args = parser.parse_args()
    check(args.current, args.history)
