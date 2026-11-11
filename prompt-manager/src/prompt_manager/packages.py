"""Version 1 format-independent package lifecycle SDK.

Backends own capture, validation, content identity, and persistence. This module
owns update policy and accepted-state history; it never imports a consumer.
Snapshots may contain bytes. Only comparison trees and state records must be JSON.
"""

from collections.abc import Iterable
from contextlib import AbstractContextManager
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .codec import canonical
from .errors import PromptError, diagnostic, operation, require
from .managed import inspection, organization, registration, search
from .managed.reconciliation import changes
from .managed.state import Managed

PACKAGE_LIFECYCLE_VERSION = 1


@runtime_checkable
class PackageBackend(Protocol):
    """Trusted local adapter, not executable configuration loaded from a store.

    The home must be dedicated to this backend's managed schema. Writes must be
    atomic; immutable writes must refuse different existing content. ``lock``
    must serialize all writers to this home. Capture/from_tree/edit must fully
    validate a package before returning it; snapshot must verify stored content.
    Store must publish complete immutable content before state can reference it.
    All methods raise PromptError, OSError, or a declared backend error.
    """

    home: Path
    schema: str
    errors: tuple[type[Exception], ...]

    def identifier(self, value: str) -> None: ...
    def check_digest(self, value: str) -> None: ...
    def lock(self) -> AbstractContextManager: ...
    def read(self, path: Path, *, missing_ok: bool = False) -> dict | None: ...
    def write(self, path: Path, row: dict, *, immutable: bool = False) -> None: ...
    def registrations(self) -> Iterable[tuple[str, str]]: ...
    def capture(self, source: Any) -> Any: ...
    def snapshot_digest(self, snapshot: Any) -> str: ...
    def store(self, snapshot: Any) -> None: ...
    def snapshot(self, identity: str) -> Any: ...
    def tree(self, snapshot: Any) -> dict: ...
    def from_tree(self, tree: dict) -> Any: ...
    def edit(self, snapshot: Any, files: dict) -> Any: ...
    def describe(self, snapshot: Any, identity: str) -> dict: ...
    def atomic_paths(self, tree: dict) -> Iterable[str]: ...


@runtime_checkable
class PackageDelivery(Protocol):
    """Optional native release/export adapter; no implicit rendering or conversion.

    Publish must retain immutable version bindings. Export must reject conflicting
    content and publish a completely verified package atomically or fail safely.
    These methods run under the backend lock and must not reacquire it.
    """

    def publish(self, snapshot: Any, version: str, status: dict) -> dict: ...
    def export(self, snapshot: Any, destination: Any, status: dict) -> dict: ...


def _validate_description(value, identity):
    require(
        isinstance(value, dict)
        and set(value) == {"digest", "name", "description"}
        and value["digest"] == identity
        and isinstance(value["name"], str)
        and 0 < len(value["name"]) <= 128
        and isinstance(value["description"], str),
        "Invalid or stale package content metadata",
        "integrity",
    )
    canonical(value)


class _Backend:
    """Add SDK policy errors without requiring consumers to translate exceptions."""

    def __init__(self, backend):
        self.backend = backend
        self.home = backend.home
        self.schema = backend.schema
        self.errors = tuple(dict.fromkeys((PromptError, OSError, *backend.errors)))

    def __getattr__(self, name):
        return getattr(self.backend, name)

    def diagnostic(self, error):
        return error.to_dict() if hasattr(error, "to_dict") else diagnostic(error)

    def validate_description(self, value, identity):
        _validate_description(value, identity)
        validator = getattr(self.backend, "validate_description", None)
        if validator is not None:
            validator(value, identity)

    def registrations(self, *, publisher=None):
        return [
            (owner, identifier)
            for owner, identifier in self.backend.registrations()
            if publisher is None or owner == publisher
        ]


class PackageLifecycle:
    """Shared lifecycle over an explicit format/storage backend.

    Construction does not create a store, capture content, or import plugins.
    This API is separate from Manager.package(), the prompt-rendering handle.
    """

    def __init__(self, backend: PackageBackend):
        require(isinstance(backend, PackageBackend), "Expected a PackageBackend adapter")
        require(
            isinstance(backend.home, Path) and backend.home.is_absolute(),
            "Package backend home must be an absolute Path",
        )
        require(
            isinstance(backend.schema, str) and bool(backend.schema),
            "Package backend must declare its managed state schema",
        )
        require(
            isinstance(backend.errors, tuple)
            and all(
                isinstance(error, type) and issubclass(error, Exception) for error in backend.errors
            ),
            "Package backend errors must be exception classes",
        )
        self._backend = _Backend(backend)
        self._state = Managed(self._backend)

    @operation
    def validate_identity(self, publisher, identifier):
        """Validate an identity without accessing the store."""
        self._state.path(publisher, identifier)

    @operation
    def validate_publisher_metadata(self, publisher_revision=None, version=None):
        self._state.publisher_metadata(publisher_revision, version)

    @operation
    def record(self, publisher, identifier, *, revision=None, missing_ok=False):
        """Read a validated raw state record for an explicit acquisition adapter."""
        return deepcopy(self._state.load(publisher, identifier, revision, missing_ok))

    @operation
    def commit_record(self, record, *, expected_revision):
        """Commit under the caller-held backend lock, rejecting stale observations.

        For trusted acquisition adapters which already publish native content.
        The caller must hold the same lock used by all lifecycle writers. None
        means create-only. Native content is verified before pointer publication.
        """
        require(isinstance(record, dict), "Expected a package state record")
        require(
            "publisher_id" in record and "id" in record and "active_digest" in record,
            "Package state identity and active content are required",
        )
        previous = self._state.load(record["publisher_id"], record["id"], missing_ok=True)
        require(
            (previous["revision"] if previous else None) == expected_revision,
            "Package state changed before acquisition commit",
            "conflict",
        )
        snapshot = self._backend.snapshot(record["active_digest"])
        # Source records need not gain metadata just because they were observed.
        # Existing summaries must follow active content when acquisition changes it.
        summary = snapshot if "content_metadata" in record else None
        return self._state.commit(deepcopy(record), previous, snapshot=summary)

    @operation
    def ensure_registered(
        self, publisher, identifier, source, *, publisher_revision=None, version=None
    ):
        return self._state.ensure(publisher, identifier, source, publisher_revision, version)

    @operation
    def register(self, publisher, identifier, source, *, publisher_revision=None, version=None):
        """Return captured effective content; retain a verified copy on refresh failure."""
        return registration.register(
            self._state, publisher, identifier, source, publisher_revision, version
        )

    @operation
    def create(self, publisher, identifier, source):
        """Create an independent local package without publisher tracking."""
        self._state.path(publisher, identifier)
        with self._backend.lock():
            require(
                self._state.load(publisher, identifier, missing_ok=True) is None,
                "Package already exists",
                "conflict",
            )
            snapshot = self._backend.capture(source)
            self._backend.store(snapshot)
            identity = self._backend.snapshot_digest(snapshot)
            baseline = {"digest": identity, "revision": None, "version": None}
            return self._state.commit(
                {
                    "schema": self._backend.schema,
                    "publisher_id": publisher,
                    "id": identifier,
                    "baseline": baseline,
                    "upstream": dict(baseline),
                    "active_digest": identity,
                    "sync_error": None,
                    "tracking": False,
                },
                None,
                snapshot=snapshot,
            )

    @operation
    def status(self, publisher, identifier, *, revision=None):
        return self._state.status(publisher, identifier, revision)

    @operation
    def resolve(self, publisher, identifier, *, revision=None):
        status = self._state.status(publisher, identifier, revision)
        snapshot = self._backend.snapshot(status["active_digest"])
        return {
            "schema": "prompt-manager/package-resolution-1",
            "snapshot": snapshot,
            "digest": status["active_digest"],
            "resolution": status,
        }

    @operation
    def catalog(self, *, verify=True, include_archived=False):
        return self._state.catalog(verify=verify, include_archived=include_archived)

    @operation
    def save(self, publisher, identifier, files, *, expected_revision):
        return self._state.edit(publisher, identifier, deepcopy(files), expected_revision)

    @operation
    def validate(self, publisher, identifier, files=None, *, revision=None):
        resolved = self.resolve(publisher, identifier, revision=revision)
        snapshot = resolved["snapshot"]
        if files is not None:
            snapshot = self._backend.edit(snapshot, deepcopy(files))
        return {
            "valid": True,
            "revision": resolved["resolution"]["revision"],
            "digest": self._backend.snapshot_digest(snapshot),
        }

    @operation
    def preview_reconciliation(self, publisher, identifier, *, resolutions=None, take=None):
        return self._state.preview(publisher, identifier, resolutions, take)

    @operation
    def view(self, publisher, identifier, **options):
        """Bounded file/review previews; binary bodies are represented by size/hash."""
        return inspection.view(self._state, publisher, identifier, **options)

    @operation
    def reconcile(self, publisher, identifier, *, expected_revision, resolutions=None, take=None):
        return self._state.reconcile(publisher, identifier, expected_revision, resolutions, take)

    @operation
    def history(self, publisher, identifier):
        return self._state.history(publisher, identifier)

    @operation
    def rollback(self, publisher, identifier, revision, *, expected_revision):
        return self._state.rollback(publisher, identifier, revision, expected_revision)

    @operation
    def compare(self, publisher, identifier, before_revision, after_revision=None):
        before = self.resolve(publisher, identifier, revision=before_revision)
        after = (
            before
            if after_revision == before_revision
            else self.resolve(publisher, identifier, revision=after_revision)
        )
        return {
            "before": before["resolution"],
            "after": after["resolution"],
            "changes": changes(
                self._backend.tree(before["snapshot"]), self._backend.tree(after["snapshot"])
            ),
        }

    @operation
    def set_tracking(self, publisher, identifier, enabled, *, expected_revision):
        return self._state.set_tracking(publisher, identifier, enabled, expected_revision)

    @operation
    def report_sync_error(self, publisher, identifier, message, *, code="io"):
        return self._state.report_error(publisher, identifier, message, code)

    @operation
    def adopt_legacy(
        self,
        publisher,
        identifier,
        legacy_source,
        publisher_source,
        *,
        publisher_revision=None,
        version=None,
        expected_revision=None,
    ):
        return self._state.adopt(
            publisher,
            identifier,
            legacy_source,
            publisher_source,
            publisher_revision=publisher_revision,
            version=version,
            expected_revision=expected_revision,
        )

    @operation
    def set_tags(self, publisher, identifier, tags, *, expected_revision):
        return organization.set_tags(self._state, publisher, identifier, tags, expected_revision)

    @operation
    def rename(self, publisher, identifier, label, *, expected_revision):
        """Change a display label, preserving native package names and content."""
        return organization.rename(self._state, publisher, identifier, label, expected_revision)

    @operation
    def set_archived(self, publisher, identifier, enabled, *, expected_revision):
        return organization.set_archived(
            self._state, publisher, identifier, enabled, expected_revision
        )

    @operation
    def search(self, query="", **filters):
        return search.search(self._state, query, **filters)

    @operation
    def refresh_metadata(self, publisher, identifier, *, expected_revision):
        return organization.refresh_metadata(self._state, publisher, identifier, expected_revision)

    @operation
    def release(self, publisher, identifier, version, *, revision=None):
        require(isinstance(version, str) and bool(version), "Release version must be nonempty text")
        require(
            isinstance(self._backend.backend, PackageDelivery),
            "Backend does not provide native publication/export",
            "unsupported",
        )
        with self._backend.lock():
            resolved = self.resolve(publisher, identifier, revision=revision)
            return self._backend.publish(resolved["snapshot"], version, resolved["resolution"])

    @operation
    def export(self, publisher, identifier, destination, *, revision=None):
        require(
            isinstance(self._backend.backend, PackageDelivery),
            "Backend does not provide native publication/export",
            "unsupported",
        )
        with self._backend.lock():
            resolved = self.resolve(publisher, identifier, revision=revision)
            return self._backend.export(resolved["snapshot"], destination, resolved["resolution"])
