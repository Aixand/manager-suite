"""Select product CI from changed paths, including downstream consumers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from manager_profiles import COMMANDS, PROFILES

ROOT = Path(__file__).resolve().parents[1]
PRODUCTS = tuple(COMMANDS)
PATHS = {name: name for name in PRODUCTS}


def consumers(products):
    result = set(products)
    while True:
        expanded = result | {
            name
            for name, dependencies in PROFILES.items()
            if result.intersection(dependencies)
        }
        if expanded == result:
            return sorted(result)
        result = expanded


def affected(paths):
    selected = set()
    for value in paths:
        path = Path(value).as_posix()
        parts = Path(path).parts
        if not parts:
            continue
        owner = parts[0]
        # Prompt-pack Markdown is executable product input, not repository prose.
        if (
            "docs" in parts
            and Path(path).suffix
            in {".md", ".png", ".svg", ".jpg", ".webp", ".gif", ".mp4"}
            or Path(path).suffix == ".md"
            and len(parts) <= 2
            or path.startswith(".github/ISSUE_TEMPLATE/")
        ):
            continue
        if owner in PRODUCTS:
            selected.add(owner)
        elif owner == "provider-policy":
            selected.update(("agent-manager", "conversation-manager"))
        elif (
            path.startswith((".github/workflows/", "scripts/", "tests/tooling/"))
            or path
            in {
                "installation.toml",
                "installer/pyproject.toml",
                "installer/uv.lock",
                "pyproject.toml",
                "uv.lock",
                ".github/ci/dependencies.json",
            }
            or owner == "installer"
        ):
            return sorted(PRODUCTS)
        elif Path(path).suffix not in {".md", ".png", ".svg"} and owner not in {
            ".github",
            ".editorconfig",
            ".gitignore",
            "LICENSE",
            "NOTICE",
        }:
            # Unknown operational inputs must not silently skip checks.
            return sorted(PRODUCTS)
    return consumers(selected)


def changed_paths(base, head, root=ROOT):
    if not base or set(base) == {"0"}:
        return None
    try:
        data = subprocess.check_output(
            ["git", "diff", "--name-only", "--no-renames", "-z", base, head],
            cwd=root,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return None
    return data.decode().split("\0")[:-1]


def plan(product="affected", *, paths=None, full=False):
    selected = (
        sorted(PRODUCTS)
        if product == "all" or product == "affected" and paths is None
        else affected(paths)
        if product == "affected"
        else [product]
    )
    if any(name not in PRODUCTS for name in selected):
        raise ValueError("Unknown CI product")
    versions = ["3.11", "3.14"] if full else ["3.11"]
    return {
        "include": [
            {"product": name, "python": version}
            for name in selected
            for version in versions
        ]
    }


def output(values, destination):
    if any(
        isinstance(value, str) and ("\n" in value or "\r" in value)
        for value in values.values()
    ):
        raise ValueError("Workflow outputs must fit on one line")
    if destination:
        with Path(destination).open("a") as stream:
            for key, value in values.items():
                stream.write(
                    f"{key}={value if isinstance(value, str) else json.dumps(value, separators=(',', ':'))}\n"
                )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--product", choices=["affected", "all", *PRODUCTS], default="affected"
    )
    parser.add_argument("--base")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--github-output")
    args = parser.parse_args()
    matrix = plan(
        args.product, paths=changed_paths(args.base, args.head), full=args.full
    )
    output(
        {"matrix": matrix, "has-products": str(bool(matrix["include"])).lower()},
        args.github_output,
    )
    print(json.dumps(matrix))


if __name__ == "__main__":
    main()
