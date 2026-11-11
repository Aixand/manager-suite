"""Materialize prompt files for filesystem authoring and staged validation."""

import json

from ..prompts.definitions import asset_path


def write_draft(snapshot, directory):
    """Write authoring files; declared release dependencies remain references."""
    (directory / "pack.json").write_text(json.dumps(snapshot["pack"]), encoding="utf-8")
    for path, content in snapshot["files"].items():
        target = directory / asset_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
