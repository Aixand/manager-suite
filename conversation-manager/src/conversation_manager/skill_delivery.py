"""Verified native skill inputs; never flatten packages into instructions."""

import hashlib
import stat
from pathlib import Path

from .models import ConversationError, digest


def verify_export(row):
    try:
        root, manifest = Path(row["export"]), row["manifest"]
        if (
            not root.is_absolute()
            or root.is_symlink()
            or not root.is_dir()
            or digest(manifest) != row["digest"]
        ):
            raise ValueError()
        files, directories = {}, []
        for path in root.rglob("*"):
            mode = path.lstat().st_mode
            relative = path.relative_to(root).as_posix()
            if stat.S_ISDIR(mode):
                directories.append(relative)
            elif stat.S_ISREG(mode):
                data = path.read_bytes()
                files[relative] = dict(
                    path=relative,
                    size=len(data),
                    digest="sha256:" + hashlib.sha256(data).hexdigest(),
                    executable=bool(mode & 0o111),
                )
            else:
                raise ValueError()
        if (
            sorted(directories) != manifest["directories"]
            or [files[k] for k in sorted(files)] != manifest["files"]
        ):
            raise ValueError()
        if (
            root.name != row["name"]
            or manifest["name"] != row["name"]
            or "SKILL.md" not in files
        ):
            raise ValueError()
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ConversationError(
            "integrity", "Required native skill export is missing or corrupt"
        ) from exc


def input_items(skills):
    return [
        {
            "type": "skill",
            "name": s["name"],
            "path": str(Path(s["export"]) / "SKILL.md"),
        }
        for s in skills
    ]


async def require_native_catalog(rpc, cwd, skills):
    if not skills:
        return
    try:
        result = await rpc("skills/list", {"cwds": [str(cwd)], "forceReload": True})
    except Exception as exc:
        if isinstance(exc, PermissionError):
            raise ConversationError("permission", "Native skill inventory cannot be read") from exc
        if getattr(exc, "uncertain", False):
            raise ConversationError("transport", "Native skill inventory could not be verified", uncertain=True) from exc
        raise ConversationError(
            "unsupported",
            "Cannot verify required native skill discovery before session dispatch",
        ) from exc
    discovered = {
        str(Path(s["path"]).resolve())
        for group in result.get("data", [])
        if group.get("cwd") == str(cwd)
        for s in group.get("skills", [])
        if s.get("enabled") is True and isinstance(s.get("path"), str)
    }
    if any(
        str((Path(s["export"]) / "SKILL.md").resolve()) not in discovered
        for s in skills
    ):
        raise ConversationError(
            "unsupported",
            "Required immutable skill export is absent or disabled in the native catalog",
        )
