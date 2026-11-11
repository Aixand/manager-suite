"""Route suite and package release operations to the product installer."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--child")
    options, args = parser.parse_known_args()
    selectable = {
        "build",
        "package",
        "install",
        "reinstall",
        "upgrade",
        "inspect",
        "plan",
        "rollback",
        "uninstall",
        "release-check",
        "export-release",
    }
    if (
        options.child
        and args
        and args[0] in selectable
        and not any(arg.split("=")[0] in {"--product", "--component"} for arg in args)
    ):
        args += ["--product", f"manager-suite/{options.child}"]
    return subprocess.call(
        [
            sys.executable,
            "-B",
            str(ROOT / "scripts/dev/cli.py"),
            "--project",
            str(ROOT),
            *args,
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
