"""Source registration ownership and discovery descriptor access."""

import os
from collections.abc import Sequence
from pathlib import Path

from ..applications import registration
from ..codec import clone, digest
from ..errors import diagnostic, require
from ..prompts.definitions import name
from ..storage.files import atomic_json, sync_directory
from ..storage.locks import lock
from ..storage.records import UNSET, check_revision, read_record


def source_descriptor(row):
    """Source records have writable local-authoring defaults."""
    require(isinstance(row.get("paths"), list) and bool(row["paths"]), "Invalid source paths")
    require(
        all(
            isinstance(value, str) and "\0" not in value and Path(value).is_absolute()
            for value in row["paths"]
        ),
        "Source paths must be absolute and contain no NUL characters",
    )
    require(type(row.get("writable", True)) is bool, "Source writable must be a boolean")
    normalized = {
        "schema": row["schema"],
        "id": row["id"],
        "label": row["label"],
        "paths": sorted({str(Path(value)) for value in row["paths"]}),
        "owner": clone(row.get("owner")),
        "writable": row.get("writable", True),
    }
    revision = digest(normalized)
    require(
        "revision" not in row or row["revision"] == revision,
        "Source registration revision mismatch",
        "integrity",
    )
    return {**normalized, "revision": revision}


class Registry:
    def __init__(self, home):
        self.home = Path(home).expanduser().resolve()

    def source(
        self, identifier, paths, *, label=None, owner=None, writable=True, expected_revision=UNSET
    ):
        name(identifier, "source identifier")
        require(identifier not in {"configured", "releases"}, "Reserved source identifier")
        require(
            isinstance(paths, Sequence)
            and not isinstance(paths, (str, bytes, bytearray))
            and bool(paths),
            "A source requires pack directories",
        )
        require(label is None or isinstance(label, str), "Source label must be text")
        require(type(writable) is bool, "Source writable must be a boolean")
        require(
            all(
                isinstance(path, (str, os.PathLike)) and "\0" not in os.fspath(path)
                for path in paths
            ),
            "Source paths must be paths without NUL characters",
        )
        row = {
            "schema": "prompt-manager/source-1",
            "id": identifier,
            "label": label or identifier,
            "paths": sorted({str(Path(path).expanduser().resolve()) for path in paths}),
        }
        if expected_revision is not UNSET or owner is not None or not writable:
            row = source_descriptor({**row, "owner": owner, "writable": writable})
        with lock(self.home, "registry"):
            if expected_revision is not UNSET:
                check_revision(self.get("sources", identifier, missing_ok=True), expected_revision)
            atomic_json(self.home / "sources" / f"{identifier}.json", row)
        return row

    def application(
        self, identifier, *, prompts, label=None, fallback_policy="none", expected_revision=None
    ):
        return registration.application(
            self,
            identifier,
            prompts=prompts,
            label=label,
            fallback_policy=fallback_policy,
            expected_revision=expected_revision,
        )

    def set_prompt(self, identifier, logical_prompt, selection, *, expected_revision):
        return registration.set_prompt(
            self, identifier, logical_prompt, selection, expected_revision=expected_revision
        )

    def get(self, kind, identifier, *, missing_ok=False):
        require(kind in {"sources", "applications"}, "Unknown registration kind")
        name(identifier, "registration identifier")
        row = read_record(self.home, kind, identifier, missing_ok=missing_ok)
        if row is None:
            return None
        require(isinstance(row, dict), "Registration must be an object")
        require(row.get("id") == identifier, "Registration identity mismatch")
        require(row.get("schema") == f"prompt-manager/{kind[:-1]}-1", "Invalid registration schema")
        require(isinstance(row.get("label"), str), "Registration label must be text")
        if kind == "sources":
            return source_descriptor(row)
        if kind == "applications":
            return registration.validate_descriptor(row)

    def list(self, kind):
        require(kind in {"sources", "applications"}, "Unknown registration kind")
        rows = []
        for path in sorted((self.home / kind).glob("*.json")):
            try:
                row = self.get(kind, path.stem)
                rows.append(row)
            except (ValueError, KeyError, TypeError, OSError) as error:
                rows.append(
                    {
                        "id": path.stem,
                        "label": path.stem,
                        "error": str(error),
                        "diagnostic": diagnostic(error),
                    }
                )
        return rows

    def remove(self, kind, identifier):
        require(kind in {"sources"}, "Unknown registration kind")
        name(identifier, "registration identifier")
        with lock(self.home, "registry"):
            path = self.home / kind / f"{identifier}.json"
            try:
                path.unlink()
            except FileNotFoundError:
                return
            sync_directory(path.parent)

    def remove_guarded(self, kind, identifier, *, expected_revision):
        require(kind in {"sources", "applications"}, "Unknown registration kind")
        name(identifier, "registration identifier")
        with lock(self.home, "registry"):
            current = self.get(kind, identifier)
            check_revision(current, expected_revision)
            path = self.home / kind / f"{identifier}.json"
            path.unlink()
            sync_directory(path.parent)
        return current
