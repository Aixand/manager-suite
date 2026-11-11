"""Publisher registration, content adoption, and the prompt-format backend."""

from copy import deepcopy
from pathlib import Path

from ..codec import MAX_BYTES, canonical, digest
from ..errors import PromptError, diagnostic, require
from ..library.snapshots import accept_tree, capture_publisher
from ..prompts.definitions import DIGEST, name
from ..prompts.examples import check_examples
from ..prompts.validation import validate_snapshot
from ..storage.files import read_json
from ..storage.locks import lock
from ..storage.objects import load_object, publish_immutable, store_object
from ..storage.records import write_record
from . import editing, updates
from .metadata import describe, validate_description


class Backend:
    schema = "prompt-manager/managed-1"
    errors = (PromptError, OSError)

    describe = staticmethod(describe)
    validate_description = staticmethod(validate_description)

    def atomic_paths(self, tree):
        return ()

    def publish(self, snapshot, version, status):
        return self.releases.publish(snapshot, version)

    def export(self, snapshot, destination, status):
        path = Path(destination).resolve()
        publish_immutable(path, snapshot)
        return {
            "schema": "prompt-manager/managed-export-1",
            "path": str(path),
            "revision": status["revision"],
            "digest": status["active_digest"],
        }

    def __init__(self, releases):
        self.releases = releases
        self.home = releases.home

    def identifier(self, value):
        name(value, "managed publisher/package identifier")

    def check_digest(self, value):
        require(
            isinstance(value, str) and DIGEST.fullmatch(value),
            "Invalid managed digest",
            "integrity",
        )

    def lock(self):
        return lock(self.home, "managed")

    def read(self, path, *, missing_ok=False):
        if missing_ok and not path.exists():
            return None
        require(path.exists(), f"Managed registration not found: {path}", "not_found")
        require(path.is_file(), f"Managed record must be a regular JSON file: {path}", "integrity")
        return read_json(path)

    def write(self, path, row, *, immutable=False):
        require(len(canonical(row)) <= MAX_BYTES, "Managed record exceeds size limit", "size")
        write_record(path, row, immutable=immutable)

    def registrations(self, *, publisher=None):
        root = self.home / "managed"
        if publisher is not None:
            self.identifier(publisher)
            try:
                paths = sorted((root / publisher).iterdir())
            except FileNotFoundError:
                return []
            return [(publisher, path.stem) for path in paths if path.suffix == ".json"]
        try:
            publishers = sorted(root.iterdir())
        except FileNotFoundError:
            return []
        return [
            (publisher.name, path.stem)
            for publisher in publishers
            if publisher.is_dir()
            for path in sorted(publisher.iterdir())
            if path.suffix == ".json"
        ]

    def capture(self, source):
        return capture_publisher(source, self.releases)

    def snapshot_digest(self, snapshot):
        return digest(snapshot)

    def store(self, snapshot):
        validate_snapshot(snapshot)
        check_examples(snapshot)
        store_object(self.home, snapshot)

    def snapshot(self, identity):
        self.check_digest(identity)
        snapshot = load_object(self.home, identity)
        validate_snapshot(snapshot)
        return snapshot

    def tree(self, snapshot):
        return deepcopy(snapshot)

    def from_tree(self, tree):
        return accept_tree(tree)

    def edit(self, snapshot, files):
        return editing.edit_snapshot(self, snapshot, files)

    def _dependency(self, reference):
        snapshot, identity = self.releases.load(reference)
        return {"digest": identity["digest"], "snapshot": snapshot}


def _refresh_locked(state, publisher, identifier, source, revision, version, previous):
    """Shared ingestion policy; caller holds the managed lock."""
    try:
        return updates.ensure_content(
            state, publisher, identifier, source, revision, version, previous
        )
    except state.backend.errors as error:
        details = getattr(state.backend, "diagnostic", diagnostic)(error)
        if previous and previous["sync_error"] != details:
            state.commit({**previous, "sync_error": details}, previous)
        raise


def ensure(state, publisher, identifier, source, publisher_revision=None, version=None):
    state.path(publisher, identifier)
    state.publisher_metadata(publisher_revision, version)
    with state.backend.lock():
        previous = state.load(publisher, identifier, missing_ok=True)
        return _refresh_locked(
            state, publisher, identifier, source, publisher_revision, version, previous
        )


def register(state, publisher, identifier, source, publisher_revision=None, version=None):
    """Ingest and capture one effective revision, retaining verified usable content.

    Invalid identities/metadata still raise. Failed ingestion can fall back only
    when an existing state and its content both pass integrity checks.
    """
    state.path(publisher, identifier)
    state.publisher_metadata(publisher_revision, version)
    with state.backend.lock():
        previous = state.load(publisher, identifier, missing_ok=True)
        try:
            status = _refresh_locked(
                state, publisher, identifier, source, publisher_revision, version, previous
            )
        except state.backend.errors as error:
            if previous is None:
                raise
            status = {
                **state.status(publisher, identifier),
                "outcome": "refresh_failed",
                "sync_error": getattr(state.backend, "diagnostic", diagnostic)(error),
            }
        snapshot = state.backend.snapshot(status["active_digest"])
        return {"snapshot": snapshot, "status": status}


def adopt(
    state,
    publisher,
    identifier,
    legacy_source,
    publisher_source,
    *,
    publisher_revision=None,
    version=None,
    expected_revision=None,
):
    state.path(publisher, identifier)
    state.publisher_metadata(publisher_revision, version)
    with state.backend.lock():
        previous = state.load(publisher, identifier, missing_ok=True)
        require(
            (previous["revision"] if previous else None) == expected_revision,
            "Managed registration changed during migration",
            "conflict",
        )
        require(
            previous is None,
            "An existing managed registration cannot be migrated again",
            "conflict",
        )
        legacy, upstream = (
            state.backend.capture(legacy_source),
            state.backend.capture(publisher_source),
        )
        state.backend.store(legacy)
        state.backend.store(upstream)
        ours, theirs = (
            state.backend.snapshot_digest(legacy),
            state.backend.snapshot_digest(upstream),
        )
        publisher_snapshot = {
            "digest": theirs,
            "revision": publisher_revision,
            "version": version,
        }
        row = {
            "schema": state.backend.schema,
            "publisher_id": publisher,
            "id": identifier,
            "baseline": publisher_snapshot
            if ours == theirs
            else {"digest": ours, "revision": None, "version": "Imported legacy content"},
            "upstream": publisher_snapshot,
            "active_digest": ours,
            "sync_error": None,
            "tracking": True,
        }
        if ours != theirs:
            row["migration"] = {"baseline_known": False, "legacy_digest": ours}
        return state.commit(row, None, snapshot=legacy)
