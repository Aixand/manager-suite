"""Local prompt creation and owned imports without source services."""

from pathlib import Path

from ..codec import digest
from ..errors import require
from ..prompts.definitions import RENDERER, ROLES, SNAPSHOT_SCHEMA
from . import registration, updates


def create(state, identifier, *, content="", role="user"):
    state.path("local", identifier)
    require(isinstance(content, str), "Prompt content must be text")
    require(isinstance(role, str) and role in ROLES, "Invalid prompt role")
    snapshot = {
        "schema": SNAPSHOT_SCHEMA,
        "renderer": RENDERER,
        "pack": {
            "schema": "prompt-manager/pack-1",
            "name": identifier,
            "sections": {identifier: {"file": "body.md"}},
            "prompts": {
                identifier: {"sections": [{"id": identifier, "section": identifier, "role": role}]}
            },
        },
        "files": {"body.md": content.replace("\r\n", "\n").replace("\r", "\n")},
        "dependencies": {},
    }
    with state.backend.lock():
        require(
            state.load("local", identifier, missing_ok=True) is None,
            "A local prompt with this identifier already exists",
            "conflict",
        )
        return updates.ensure_content(
            state, "local", identifier, None, None, None, None, snapshot=snapshot
        )


def import_local(state, path, *, package_id=None):
    """Capture a pack or exported snapshot; repeat imports preserve user edits.

    Default identity follows the canonical path. Supply a stable package_id when
    moving a source. Changed unversioned content requires explicit review.
    """
    source = Path(path).resolve()
    identifier = package_id if package_id is not None else "imported-" + digest(str(source))[7:31]
    return registration.ensure(state, "local", identifier, source)
