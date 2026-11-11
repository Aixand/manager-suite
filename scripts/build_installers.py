"""Build and qualify one product or the complete suite on the current native OS."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

from manager_profiles import COMMANDS

ROOT = Path(__file__).resolve().parents[1]


def run(script, *argv):
    subprocess.run(
        [sys.executable, "-B", str(ROOT / "scripts" / script), *map(str, argv)],
        cwd=ROOT,
        check=True,
    )


def build(output, product="manager-suite"):
    if product not in (*COMMANDS, "manager-suite"):
        raise ValueError("Unknown installer product")
    output = output.resolve()
    if output.exists():
        raise RuntimeError(
            "Choose a new output directory; existing release bytes are immutable"
        )
    output.mkdir(parents=True)
    if product != "manager-suite":
        artifact = output / "products" / product
        report = output / f"{product}-verification.json"
        run(
            "manager_dev.py",
            "package",
            "--product",
            f"manager-suite/{product}",
            "--output",
            artifact,
        )
        run(
            "manager_dev.py",
            "verify",
            "--artifact",
            artifact,
            "--install",
            "--report",
            report,
        )
        run(
            "qualify_single_product.py", artifact, "--report", output / "lifecycle.json"
        )
        run("prepare_runtime.py", "--bootstrap-output", output / "bootstrap")
        run(
            "manager_dev.py",
            "export-release",
            "--artifact",
            artifact,
            "--report",
            report,
            "--output",
            output / "handoff/products" / product,
        )
        return
    aggregate = output / "aggregate"
    run("manager_dev.py", "package", "--output", aggregate)
    run(
        "manager_dev.py",
        "verify",
        "--artifact",
        aggregate,
        "--install",
        "--report",
        output / "aggregate-verification.json",
    )
    for name in COMMANDS:
        artifact = output / "products" / name
        run(
            "manager_dev.py",
            "package",
            "--product",
            "manager-suite/" + name,
            "--output",
            artifact,
        )
        run(
            "manager_dev.py",
            "verify",
            "--artifact",
            artifact,
            "--install",
            "--report",
            output / f"{name}-verification.json",
        )
    run("prepare_runtime.py", "--bootstrap-output", output / "bootstrap")
    run(
        "qualify_product_installation.py",
        aggregate,
        "--individuals",
        output / "products",
        "--bootstrap",
        output / "bootstrap",
        "--report",
        output / "lifecycle.json",
    )
    for name, artifact in [
        ("manager-suite", aggregate),
        *[(name, output / "products" / name) for name in COMMANDS],
    ]:
        report = output / (
            "aggregate-verification.json"
            if name == "manager-suite"
            else f"{name}-verification.json"
        )
        destination = output / "handoff" / "manager-suite"
        # The aggregate handoff itself must remain immutable: children are peers.
        if name != "manager-suite":
            destination = output / "handoff" / "products" / name
        run(
            "manager_dev.py",
            "export-release",
            "--artifact",
            artifact,
            "--report",
            report,
            "--output",
            destination,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--product", choices=["manager-suite", *COMMANDS], default="manager-suite"
    )
    args = parser.parse_args()
    build(args.output, args.product)
