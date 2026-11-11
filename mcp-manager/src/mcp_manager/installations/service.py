"""Explicit MCP installation into retained, immutable runtime generations."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid
import venv

from .recipes import validate

from ..sources.records import (
    SourceError,
    atomic,
    digest,
    materialize,
    read,
    relative,
    require,
)


def manifest_path(home, recipe):
    return Path(home) / "runtime-index" / (digest(validate(recipe))[7:] + ".json")


def runtime(home, recipe):
    path = manifest_path(home, recipe)
    row = read(path)
    if row is None:
        return None
    require(
        row.get("schema") == "mcp-manager/runtime-1"
        and row.get("recipe_digest") == digest(recipe)
        and row.get("digest")
        == digest({k: v for k, v in row.items() if k != "digest"}),
        "MCP runtime manifest is damaged",
        "integrity",
    )
    root = Path(home) / "runtime-generations"
    generation = Path(row["directory"])
    require(
        generation.is_relative_to(root) and generation.is_dir(),
        "MCP runtime generation is missing",
        "unavailable",
    )
    for key in ("entrypoint", "working_directory"):
        require(
            Path(row[key]).resolve().is_relative_to(generation.resolve()),
            "Runtime path leaves generation",
            "integrity",
        )
    require(
        Path(row["entrypoint"]).is_file() and Path(row["command"]).is_file(),
        "Prepared MCP executable is missing",
        "unavailable",
    )
    require(
        hashlib.sha256(Path(row["entrypoint"]).read_bytes()).hexdigest()
        == row["entrypoint_sha256"],
        "Prepared MCP entrypoint changed",
        "integrity",
    )
    return row


def resolve_definition(home, definition):
    if "installation" not in definition:
        return dict(definition), None
    recipe = definition["installation"]
    validate(recipe)
    require(
        recipe["kind"] != "manual",
        recipe.get("reason", "Complete MCP setup before launch"),
        "unavailable",
    )
    row = runtime(home, recipe)
    require(
        row is not None,
        "Install this managed MCP version before launching it",
        "unavailable",
    )
    result = {key: value for key, value in definition.items() if key != "installation"}
    result.update(
        command=row["command"], args=[*row["arguments"], *definition.get("args", [])]
    )
    result.setdefault("cwd", row["working_directory"])
    return result, row


def ready(home, snapshot):
    definition = snapshot["definition"]
    if "installation" not in definition:
        return True
    if definition["installation"]["kind"] == "manual":
        return False
    try:
        return runtime(home, definition["installation"]) is not None
    except SourceError as error:
        if error.code == "unavailable":
            return False
        raise


def status(home, definition):
    recipe = definition.get("installation")
    if recipe is None:
        available = shutil.which(definition["command"]) is not None
        return {
            "status": "ready" if available else "executable_missing",
            "external": True,
            "installation_required": False,
            "executable_available": available,
            **(
                {}
                if available
                else {
                    "reason": "Install or configure this executable on the execution machine."
                }
            ),
        }
    validate(recipe)
    if recipe["kind"] == "manual":
        return {
            "status": "setup_required",
            "installation_required": True,
            "reason": recipe.get("reason"),
        }
    try:
        row = runtime(home, recipe)
        return {
            "status": "ready" if row else "installation_required",
            "installation_required": row is None,
            "generation": row["generation"] if row else None,
            "recipe": recipe,
            "executes_build_scripts": True,
        }
    except SourceError as error:
        return {
            "status": "unavailable",
            "installation_required": True,
            "error": error.to_dict(),
            "recipe": recipe,
        }


def capture_install(
    engine,
    publisher,
    identifier,
    *,
    expected_revision,
    candidate="active",
    resolutions=None,
    take=None,
):
    row = engine.load(publisher, identifier)
    require(
        row["revision"] == expected_revision,
        "MCP changed before installation",
        "conflict",
    )
    require(
        candidate in ("active", "publisher", "reconciled"),
        "Choose the active, publisher or reconciled candidate",
    )
    require(
        candidate == "reconciled" or not (resolutions or take),
        "Reviewed choices require the reconciled candidate",
    )
    if candidate == "reconciled":
        preview = engine.preview(publisher, identifier, resolutions, take)
        require(
            not preview["conflicts"] and preview["valid"],
            "Resolve conflicts before preparing the selected runtime",
            "conflict",
        )
        snapshot = engine.backend.from_tree(preview["candidate"])
        engine.backend.store(snapshot)
        identity = engine.backend.snapshot_digest(snapshot)
    else:
        identity = (
            row["active_digest"] if candidate == "active" else row["upstream"]["digest"]
        )
        snapshot = engine.backend.snapshot(identity)
    recipe = snapshot["definition"].get("installation")
    require(
        recipe is not None, "This definition uses an externally installed executable"
    )
    validate(recipe)
    require(
        recipe["kind"] != "manual",
        recipe.get("reason", "Configure installation first"),
        "unsupported",
    )
    return {
        "publisher": publisher,
        "identifier": identifier,
        "expected_revision": expected_revision,
        "candidate": candidate,
        "content_digest": identity,
        "recipe": recipe,
    }


def install(sources, capture, job_directory, *, guard, progress):
    home = sources.home
    recipe = validate(capture["recipe"])
    existing = runtime(home, recipe)
    if existing:
        return {"runtime": existing, "outcome": "already_prepared"}
    generation_id = str(uuid.uuid4())
    directory = home / "runtime-generations" / generation_id
    directory.mkdir(parents=True, mode=0o700)
    work = directory / "work"
    work.mkdir()
    kind = recipe["kind"]
    env = dict(os.environ)
    env["npm_config_cache"] = str(directory / "npm-cache")
    env["npm_config_global"] = "false"
    env["npm_config_prefix"] = str(directory / "npm-prefix")
    env["PIP_CACHE_DIR"] = str(directory / "pip-cache")
    for name in ("PIP_TARGET", "PIP_PREFIX", "PIP_USER"):
        env.pop(name, None)
    env["PIP_REQUIRE_VIRTUALENV"] = "true"

    # Credentials are inherited from the execution machine, never written to manifests.
    def execute(argv, label, *, cwd=work):
        progress(label)
        with (job_directory / "install.log").open("ab") as output:
            result = subprocess.run(
                [str(v) for v in argv],
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                timeout=900,
                check=False,
            )
        require(
            result.returncode == 0,
            f"{label} failed (exit {result.returncode}); inspect the retained installation log",
            "installation_failed",
        )

    if kind in ("node-source", "python-source"):
        source = sources.tree(recipe["source_digest"])
        root = materialize(source, directory / "source")
        work = root / recipe.get("subdirectory", "")
        require(work.is_dir(), "Source installation directory is missing")
    if kind in ("npm", "node-source"):
        npm, node = shutil.which("npm"), shutil.which("node")
        require(
            npm and node,
            "Install Node and npm on this execution machine",
            "unavailable",
        )
        if kind == "npm":
            atomic(
                work / "package.json",
                {"name": "aixand-managed-mcp", "version": "1.0.0", "private": True},
            )
            execute(
                [
                    npm,
                    "install",
                    "--package-lock-only",
                    "--ignore-scripts",
                    "--save-exact",
                    "--no-audit",
                    "--no-fund",
                    recipe["package"] + "@" + recipe["version"],
                ],
                "Resolve exact Node package",
                cwd=work,
            )
        require(
            (work / "package-lock.json").is_file(),
            "Node source installation requires package-lock.json",
        )
        execute(
            [npm, "ci", "--no-audit", "--no-fund"],
            "Install locked Node dependencies",
            cwd=work,
        )
        package_root = (
            work / "node_modules" / recipe["package"] if kind == "npm" else work
        )
        package = read(package_root / "package.json")
        require(
            isinstance(package, dict),
            "Installed Node package metadata is missing",
            "integrity",
        )
        if kind == "node-source" and package.get("scripts", {}).get("build"):
            execute([npm, "run", "build"], "Build Node source", cwd=work)
        entry = recipe.get("entrypoint")
        if kind == "npm":
            binaries = package.get("bin")
            if entry:
                entry = (
                    binaries.get(entry, entry) if isinstance(binaries, dict) else entry
                )
            elif isinstance(binaries, str):
                entry = binaries
            else:
                require(
                    isinstance(binaries, dict) and len(binaries) == 1,
                    "Package has several or no executables; configure its entrypoint",
                )
                entry = next(iter(binaries.values()))
        require(isinstance(entry, str), "Node entrypoint must be a path")
        entry = relative(entry.removeprefix("./"))
        entrypoint = (package_root / entry).resolve()
        require(
            entrypoint.is_relative_to(directory.resolve()) and entrypoint.is_file(),
            "Node entrypoint is unavailable",
        )
        prefix = entrypoint.read_bytes()[:256]
        require(
            entrypoint.suffix in (".js", ".mjs", ".cjs")
            or b"node" in prefix.split(b"\n", 1)[0],
            "This npm executable requires a different runtime adapter",
            "unsupported",
        )
        command, arguments = str(Path(node).resolve()), [str(entrypoint)]
        lock_digest = hashlib.sha256(
            (work / "package-lock.json").read_bytes()
        ).hexdigest()
    else:
        python_home = directory / "venv"
        progress("Create isolated Python environment")
        venv.EnvBuilder(with_pip=True).create(python_home)
        bin_directory = python_home / ("Scripts" if os.name == "nt" else "bin")
        python = bin_directory / ("python.exe" if os.name == "nt" else "python")
        wheels = directory / "wheels"
        wheels.mkdir()
        package = (
            recipe["package"] + "==" + recipe["version"]
            if kind == "pypi"
            else str(work)
        )
        execute(
            [python, "-m", "pip", "wheel", "--wheel-dir", wheels, package],
            "Capture Python wheels",
            cwd=work,
        )
        require(
            any(wheels.glob("*.whl")),
            "No Python wheels were built",
            "installation_failed",
        )
        if kind == "python-source":
            import tomllib

            project = tomllib.loads((work / "pyproject.toml").read_text())["project"]
            package = project["name"]
        execute(
            [
                python,
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                wheels,
                package,
            ],
            "Install captured Python wheels",
            cwd=work,
        )
        script = recipe.get("entrypoint", "")
        relative(script)
        entrypoint = (
            bin_directory / (script + (".exe" if os.name == "nt" else ""))
        ).resolve()
        require(
            entrypoint.is_relative_to(directory.resolve()) and entrypoint.is_file(),
            "Python console script was not installed",
        )
        command, arguments = str(entrypoint), []
        lock_digest = digest(
            {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(wheels.glob("*.whl"))
            }
        )
    row = {
        "schema": "mcp-manager/runtime-1",
        "generation": generation_id,
        "recipe_digest": digest(recipe),
        "directory": str(directory),
        "command": command,
        "arguments": arguments,
        "entrypoint": str(entrypoint),
        "entrypoint_sha256": hashlib.sha256(entrypoint.read_bytes()).hexdigest(),
        "working_directory": str(work),
        "dependency_lock_digest": lock_digest,
        "created_at": time.time(),
    }
    row["digest"] = digest(row)
    with sources.backend.lock():
        guard()
        # Concurrent preparation may have won; never replace an existing generation.
        previous = runtime(home, recipe)
        if previous is None:
            atomic(manifest_path(home, recipe), row)
        else:
            row = previous
    return {
        "runtime": row,
        "outcome": "prepared",
        "activation": "Review and apply the prepared version to activate a publisher update",
    }
