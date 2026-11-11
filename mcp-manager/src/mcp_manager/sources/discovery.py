"""Detect MCP definitions and supported Node/Python launch recipes."""

from __future__ import annotations

import base64
from pathlib import PurePosixPath
import re
import tomllib

from .records import digest, loads, relative, require, slug


def text(tree, path):
    require(path in tree["files"], f"Source file missing: {path}", "not_found")
    return base64.b64decode(tree["files"][path]["content_b64"], validate=True).decode(
        "utf-8"
    )


def under(path, root):
    return not root or path == root or path.startswith(root + "/")


def discover(tree, source):
    kind = "mcp"
    root = source.get("path", "")
    relative(root, empty=True)
    rows = []
    for path in sorted(tree["files"]):
        if not under(path, root):
            continue
        name = PurePosixPath(path).name
        directory = str(PurePosixPath(path).parent)
        directory = "" if directory == "." else directory
        label = PurePosixPath(directory).name or source["repository"].split("/")[-1]
        common = {
            "path": directory,
            "name": slug(label).replace("_", "-"),
            "description": "",
            "kind": kind,
        }
        if name == "server.json":
            value = loads(text(tree, path))
            if value.get("command"):
                rows.append(
                    {**common, "id": path, "type": "definition", "definition": value}
                )
            elif isinstance(value.get("packages"), list) or isinstance(
                value.get("remotes"), list
            ):
                for index, package in enumerate(value.get("packages", [])):
                    recipe = registry_recipe(package)
                    rows.append(
                        {
                            **common,
                            "id": f"{path}#package-{index}",
                            "type": "registry",
                            "name": slug(value.get("title") or label),
                            "description": value.get("description", ""),
                            "definition": recipe,
                        }
                    )
                for index, remote in enumerate(value.get("remotes", [])):
                    rows.append(
                        {
                            **common,
                            "id": f"{path}#remote-{index}",
                            "type": "unsupported",
                            "description": f"Remote {remote.get('type', 'transport')} requires an adapter",
                            "definition": manual(
                                "Remote MCP transport is not supported by this adapter"
                            ),
                        }
                    )
    # Executable detection is a suggestion, not proof of an MCP handshake.
    if not rows:
        for path in sorted(tree["files"]):
            if not under(path, root):
                continue
            name = PurePosixPath(path).name
            directory = str(PurePosixPath(path).parent)
            directory = "" if directory == "." else directory
            if name == "package.json":
                package = loads(text(tree, path))
                bins = package.get("bin", {})
                if isinstance(bins, str):
                    bins = {package.get("name", "server").split("/")[-1]: bins}
                if isinstance(bins, dict):
                    for binary, entry in bins.items():
                        require(
                            isinstance(entry, str),
                            "Node executable paths must be strings",
                        )
                        entry = relative(entry.removeprefix("./"))
                        rows.append(
                            {
                                "id": f"{path}#{binary}",
                                "path": directory,
                                "name": slug(binary),
                                "kind": kind,
                                "type": "node-source",
                                "description": "Detected Node executable; verify it implements MCP",
                                "definition": installed(
                                    {
                                        "kind": "node-source",
                                        "entrypoint": entry,
                                        "subdirectory": directory,
                                        "source_digest": digest(tree),
                                    }
                                ),
                            }
                        )
            elif name == "pyproject.toml":
                package = tomllib.loads(text(tree, path))
                for binary, entry in (
                    package.get("project", {}).get("scripts", {}).items()
                ):
                    rows.append(
                        {
                            "id": f"{path}#{binary}",
                            "path": directory,
                            "name": slug(binary),
                            "kind": kind,
                            "type": "python-source",
                            "description": "Detected Python executable; verify it implements MCP",
                            "definition": installed(
                                {
                                    "kind": "python-source",
                                    "entrypoint": binary,
                                    "subdirectory": directory,
                                    "source_digest": digest(tree),
                                }
                            ),
                        }
                    )
        if not rows:
            rows.append(
                {
                    "id": root or "@repository",
                    "path": root,
                    "name": slug(source["repository"].split("/")[-1]),
                    "kind": kind,
                    "type": "manual",
                    "description": "Configure a launch method to use this source",
                    "definition": manual(
                        "No supported server definition was discovered", digest(tree)
                    ),
                }
            )
    require(
        len(rows) <= 256,
        "Too many resources; select a narrower source directory",
        "size",
    )
    for row in rows:
        prefix = row["path"] + "/" if row["path"] else ""
        paths = [p for p in tree["files"] if p.startswith(prefix)]
        row["files"] = paths[:32]
        row["file_count"] = len(paths)
    return rows


def installed(recipe, *, args=None, env=None):
    return {
        "kind": "stdio",
        "command": "aixand-managed-runtime",
        "args": args or [],
        "env_vars": env or [],
        "installation": {"schema": "mcp-manager/installation-1", **recipe},
    }


def manual(reason, identity=None):
    return installed(
        {
            "kind": "manual",
            "reason": reason,
            **({"source_digest": identity} if identity else {}),
        }
    )


def registry_recipe(package):
    registry = package.get("registryType")
    version = package.get("version", "")
    if (
        registry not in ("npm", "pypi")
        or not isinstance(version, str)
        or not re.fullmatch(r"[0-9][A-Za-z0-9.+_-]{0,127}", version)
        or package.get("registryBaseUrl")
        or (package.get("transport") or {}).get("type", "stdio") != "stdio"
    ):
        return manual("This package installation method is not supported")
    args = []
    for arg in package.get("packageArguments", []):
        if (
            arg.get("value") is None
            or arg.get("isRepeated")
            or arg.get("type") == "positional"
            and "name" not in arg
            and "value" not in arg
        ):
            return manual("Configure the package arguments before installation")
        if arg.get("type") == "named":
            args.append(arg["name"])
        args.append(str(arg["value"]))
    if package.get("runtimeArguments"):
        return manual(
            "Custom runtime arguments require a reviewed launch configuration"
        )
    env = [
        item["name"]
        for item in package.get("environmentVariables", [])
        if isinstance(item, dict) and "name" in item
    ]
    recipe = {
        "kind": registry,
        "package": package.get("identifier", ""),
        "version": version,
    }
    if registry == "pypi":
        recipe["entrypoint"] = package.get("identifier", "").replace("_", "-")
    return installed(recipe, args=args, env=env)
