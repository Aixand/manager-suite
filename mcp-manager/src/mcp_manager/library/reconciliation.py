"""Owned revisions and three-way reconciliation.

Immutable publisher state and structural reconciliation; the storage adapter
lives in library/backend.py.
"""

from copy import deepcopy

from ..storage.codec import digest
from ..errors import require

MISSING = object()


def same(left, right):
    if left is MISSING or right is MISSING:
        return left is right
    return digest(left) == digest(right)


def pointer(path, key):
    return path + "/" + key.replace("~", "~0").replace("/", "~1")


def side(value):
    return {"present": False} if value is MISSING else {"present": True, "value": value}


def merge(base, local, incoming, resolutions=None, *, baseline_known=True):
    resolutions = {} if resolutions is None else resolutions
    require(isinstance(resolutions, dict), "Resolutions must map conflict paths to choices")
    conflicts, used = [], set()

    def walk(before, ours, theirs, path):
        if same(ours, theirs) or (baseline_known and same(before, theirs)):
            return ours
        if baseline_known and same(before, ours):
            return theirs
        # Captured dependency snapshots and their digests must stay together.
        dependency = path.startswith("/dependencies/") and path.count("/") == 2
        if (
            isinstance(ours, dict)
            and isinstance(theirs, dict)
            and (isinstance(before, dict) or not baseline_known)
            and not dependency
        ):
            before = before if isinstance(before, dict) else {}
            result = {}
            for key in sorted(before.keys() | ours.keys() | theirs.keys()):
                value = walk(
                    before.get(key, MISSING),
                    ours.get(key, MISSING),
                    theirs.get(key, MISSING),
                    pointer(path, key),
                )
                if value is not MISSING:
                    result[key] = value
            return result
        if path in resolutions:
            used.add(path)
            choice = resolutions[path]
            require(isinstance(choice, dict), "Each conflict choice must be an object")
            if set(choice) == {"take"}:
                require(
                    isinstance(choice["take"], str)
                    and choice["take"] in {"managed", "publisher", "baseline"},
                    "Invalid conflict choice",
                )
                return {"managed": ours, "publisher": theirs, "baseline": before}[choice["take"]]
            if choice == {"delete": True}:
                return MISSING
            require(set(choice) == {"value"}, "Use take, delete, or value for a conflict")
            return choice["value"]
        conflicts.append(
            {
                "path": path,
                "baseline": side(before) if baseline_known else {"present": False},
                "managed": side(ours),
                "publisher": side(theirs),
            }
        )
        return ours

    candidate = walk(base, local, incoming, "")
    require(
        set(resolutions) == used,
        "Resolution contains a path that is not a current conflict",
        "conflict",
    )
    return deepcopy(candidate), conflicts


def changes(before, after, path=""):
    if same(before, after):
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        return [
            change
            for key in sorted(before.keys() | after.keys())
            for change in changes(
                before.get(key, MISSING), after.get(key, MISSING), pointer(path, key)
            )
        ]
    return [{"path": path, "before": side(before), "after": side(after)}]


class Managed:
    def __init__(self, backend):
        self.backend = backend

    def path(self, publisher, identifier):
        self.backend.identifier(publisher)
        self.backend.identifier(identifier)
        return self.backend.home / "managed" / publisher / (identifier + ".json")

    def load(self, publisher, identifier, revision=None, missing_ok=False):
        path = self.path(publisher, identifier)
        if revision is not None:
            self.backend.check_digest(revision)
            path = (
                self.backend.home
                / "managed-history"
                / publisher
                / identifier
                / (revision[7:] + ".json")
            )
        row = self.backend.read(path, missing_ok=missing_ok)
        if row is None:
            return None
        keys = {
            "schema",
            "publisher_id",
            "id",
            "baseline",
            "upstream",
            "active_digest",
            "sequence",
            "previous",
            "sync_error",
            "tracking",
            "revision",
        }
        require(
            isinstance(row, dict) and keys <= set(row) <= keys | {"migration", "source", "source_review", "hold", "label"},
            "Invalid managed registration",
            "integrity",
        )
        require(
            row["schema"] == self.backend.schema
            and row["publisher_id"] == publisher
            and row["id"] == identifier,
            "Managed identity mismatch",
            "integrity",
        )
        require(
            row["revision"] == digest({k: v for k, v in row.items() if k != "revision"}),
            "Managed revision mismatch",
            "integrity",
        )
        require(
            revision is None or row["revision"] == revision, "Managed history mismatch", "integrity"
        )
        self.backend.check_digest(row["active_digest"])
        require(
            type(row["sequence"]) is int and row["sequence"] >= 1,
            "Invalid managed sequence",
            "integrity",
        )
        require(
            type(row["tracking"]) is bool
            and (row["sync_error"] is None or isinstance(row["sync_error"], dict)),
            "Invalid tracking/error state",
            "integrity",
        )
        if row["previous"] is not None:
            self.backend.check_digest(row["previous"])
        if 'source' in row:
            from ..sources.records import SourceError, validate_source
            try:
                validate_source(row['source'])
            except SourceError as error:
                require(False, str(error), 'integrity')
        for flag in ('hold', 'source_review'):
            require(flag not in row or type(row[flag]) is bool, 'Invalid managed state flag', 'integrity')
        require('label' not in row or isinstance(row['label'], str) and 0 < len(row['label']) <= 128,
                'Invalid managed label', 'integrity')
        migration = row.get("migration")
        require(
            migration is None
            or (
                isinstance(migration, dict)
                and set(migration) == {"baseline_known", "legacy_digest"}
                and migration["baseline_known"] is False
            ),
            "Invalid migration state",
            "integrity",
        )
        if migration:
            self.backend.check_digest(migration["legacy_digest"])
        for key in ("baseline", "upstream"):
            value = row[key]
            require(
                isinstance(value, dict) and set(value) == {"digest", "revision", "version"},
                "Invalid publisher snapshot",
                "integrity",
            )
            self.backend.check_digest(value["digest"])
            self.publisher_metadata(value["revision"], value["version"])
        return row

    def publisher_metadata(self, revision, version):
        require(
            revision is None or (type(revision) is int and revision >= 0),
            "publisher_revision must be a nonnegative integer or null",
        )
        require(
            version is None or (isinstance(version, str) and len(version) <= 128),
            "version must be text of at most 128 characters or null",
        )

    def status(self, publisher, identifier, revision=None):
        row = self.load(publisher, identifier, revision)
        pending = bool(row.get("source_review")) or row["upstream"]["digest"] != row["baseline"]["digest"]
        return {
            **row,
            **(self.backend.status_details(row) if hasattr(self.backend, "status_details") else {}),
            "customized": bool(row.get("migration")) or bool(row.get("hold"))
            or row["active_digest"] != row["baseline"]["digest"],
            "baseline_known": not bool(row.get("migration")),
            "reconciliation_required": pending,
            "update_status": "reconciliation_required" if pending else "current",
        }

    def commit(self, row, previous):
        if row.get('source'):
            from ..sources.records import SourceError, validate_source
            try:
                validate_source(row['source'])
            except SourceError as error:
                require(False, str(error), 'integrity')
        row = deepcopy(row)
        row.pop("revision", None)
        row["sequence"] = previous["sequence"] + 1 if previous else 1
        row["previous"] = previous["revision"] if previous else None
        row["revision"] = digest(row)
        history = (
            self.backend.home
            / "managed-history"
            / row["publisher_id"]
            / row["id"]
            / (row["revision"][7:] + ".json")
        )
        self.backend.write(history, row, immutable=True)
        self.backend.write(self.path(row["publisher_id"], row["id"]), row)
        return self.status(row["publisher_id"], row["id"])

    def guarded(self, publisher, identifier, expected_revision):
        row = self.load(publisher, identifier)
        require(
            expected_revision == row["revision"],
            "Managed content or update changed; refresh before applying",
            "conflict",
        )
        return row

    def ensure(self, publisher, identifier, source, publisher_revision=None, version=None):
        self.path(publisher, identifier)
        self.publisher_metadata(publisher_revision, version)
        with self.backend.lock():
            previous = self.load(publisher, identifier, missing_ok=True)
            try:
                return self._ensure(
                    publisher, identifier, source, publisher_revision, version, previous
                )
            except self.backend.errors as error:
                if previous:
                    diagnostic = (
                        error.to_dict()
                        if hasattr(error, "to_dict")
                        else {"code": "io", "message": str(error)}
                    )
                    if previous["sync_error"] != diagnostic:
                        self.commit({**previous, "sync_error": diagnostic}, previous)
                raise

    def _ensure(self, publisher, identifier, source, revision, version, previous):
        if previous and not previous["tracking"]:
            return {**self.status(publisher, identifier), "outcome": "tracking_stopped"}
        require(not previous or not previous.get("source"), "GitHub resources must use check_updates", "conflict")
        old_revision = previous["upstream"]["revision"] if previous else None
        if old_revision is not None:
            require(
                revision is not None,
                "An ordered publisher must supply publisher_revision",
                "conflict",
            )
            if revision < old_revision:
                return {**self.status(publisher, identifier), "outcome": "ignored_older"}
        snapshot = self.backend.capture(source)
        identity = self.backend.snapshot_digest(snapshot)
        incoming = {"digest": identity, "revision": revision, "version": version}
        if previous and revision is not None and revision == old_revision:
            require(
                identity == previous["upstream"]["digest"],
                "Publisher revision was reused with different content",
                "conflict",
            )
        self.backend.store(snapshot)
        if previous is None:
            row = {
                "schema": self.backend.schema,
                "publisher_id": publisher,
                "id": identifier,
                "baseline": incoming,
                "upstream": incoming,
                "active_digest": identity,
                "sync_error": None,
                "tracking": True,
            }
            return {**self.commit(row, None), "outcome": "imported"}
        row = {**previous, "upstream": incoming, "sync_error": None}
        outcome = "unchanged"
        if identity == previous["upstream"]["digest"]:
            if identity == previous["baseline"]["digest"]:
                row["baseline"] = incoming
        elif identity in {previous["active_digest"], previous["baseline"]["digest"]} or (
            revision is not None
            and not previous.get("hold")
            and getattr(self.backend, "activation_ready", lambda _: True)(snapshot)
            and not previous.get("migration")
            and previous["active_digest"] == previous["baseline"]["digest"]
        ):
            if (
                not previous.get("migration")
                and previous["active_digest"] == previous["baseline"]["digest"]
            ):
                row["active_digest"] = identity
            row["baseline"] = incoming
            outcome = "updated"
        else:
            outcome = "update_pending"
        if (
            row.get("migration")
            and identity == row["active_digest"]
            and row["baseline"]["digest"] == identity
        ):
            row["migration"] = None
        if all(row[key] == previous[key] for key in row if key != "revision"):
            return {**self.status(publisher, identifier), "outcome": outcome}
        return {**self.commit(row, previous), "outcome": outcome}

    def adopt(
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
        self.path(publisher, identifier)
        self.publisher_metadata(publisher_revision, version)
        with self.backend.lock():
            previous = self.load(publisher, identifier, missing_ok=True)
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
                self.backend.capture(legacy_source),
                self.backend.capture(publisher_source),
            )
            self.backend.store(legacy)
            self.backend.store(upstream)
            ours, theirs = (
                self.backend.snapshot_digest(legacy),
                self.backend.snapshot_digest(upstream),
            )
            publisher_snapshot = {
                "digest": theirs,
                "revision": publisher_revision,
                "version": version,
            }
            row = {
                "schema": self.backend.schema,
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
            return self.commit(row, None)

    def edit(self, publisher, identifier, files, expected_revision):
        with self.backend.lock():
            row = self.guarded(publisher, identifier, expected_revision)
            snapshot = self.backend.edit(self.backend.snapshot(row["active_digest"]), files)
            self.backend.store(snapshot)
            identity = self.backend.snapshot_digest(snapshot)
            if identity == row["active_digest"]:
                return self.status(publisher, identifier)
            return self.commit({**row, "active_digest": identity}, row)

    def preview(self, publisher, identifier, resolutions=None, take=None):
        row = self.load(publisher, identifier)
        require(
            row["upstream"]["digest"] != row["baseline"]["digest"], "No pending update", "conflict"
        )
        trees = {
            key: self.backend.tree(self.backend.snapshot(identity))
            for key, identity in {
                "baseline": row["baseline"]["digest"],
                "managed": row["active_digest"],
                "publisher": row["upstream"]["digest"],
            }.items()
        }
        if take is not None:
            require(
                isinstance(take, str) and take in {"managed", "publisher"} and not resolutions,
                "Choose take or conflict resolutions",
            )
            candidate, conflicts = trees[take], []
        else:
            candidate, conflicts = merge(
                trees["baseline"],
                trees["managed"],
                trees["publisher"],
                resolutions,
                baseline_known=not bool(row.get("migration")),
            )
        diagnostic = None
        try:
            self.backend.from_tree(candidate)
        except self.backend.errors as error:
            diagnostic = (
                error.to_dict()
                if hasattr(error, "to_dict")
                else {"code": "validation", "message": str(error)}
            )
        return {
            "revision": row["revision"],
            "baseline_known": not bool(row.get("migration")),
            "candidate": candidate,
            "conflicts": conflicts,
            "valid": diagnostic is None,
            "validation_error": diagnostic,
            "managed_changes": changes(trees["baseline"], trees["managed"]),
            "publisher_changes": changes(trees["baseline"], trees["publisher"]),
        }

    def reconcile(self, publisher, identifier, expected_revision, resolutions=None, take=None):
        with self.backend.lock():
            row = self.guarded(publisher, identifier, expected_revision)
            preview = self.preview(publisher, identifier, resolutions, take)
            require(not preview["conflicts"], "Resolve all conflicts before applying", "conflict")
            snapshot = self.backend.from_tree(preview["candidate"])
            require(self.backend.snapshot_digest(snapshot) == row["active_digest"] or getattr(self.backend, "activation_ready", lambda _: True)(snapshot), "Prepare the selected MCP runtime before applying this update", "unavailable")
            self.backend.store(snapshot)
            return self.commit(
                {
                    **row,
                    "active_digest": self.backend.snapshot_digest(snapshot),
                    "baseline": row["upstream"],
                    **({"source_review": False} if "source_review" in row else {}),
                    **({"hold": False} if "hold" in row else {}),
                    **({"migration": None} if row.get("migration") else {}),
                },
                row,
            )

    def rollback(self, publisher, identifier, revision, expected_revision):
        with self.backend.lock():
            row = self.guarded(publisher, identifier, expected_revision)
            historical = self.load(publisher, identifier, revision)
            self.backend.snapshot(historical["active_digest"])
            return self.commit({**row, "active_digest": historical["active_digest"], "hold": True}, row)

    def set_tracking(self, publisher, identifier, enabled, expected_revision):
        require(type(enabled) is bool, "tracking must be a boolean")
        with self.backend.lock():
            row = self.guarded(publisher, identifier, expected_revision)
            if row["tracking"] == enabled:
                return self.status(publisher, identifier)
            return self.commit({**row, "tracking": enabled}, row)

    def report_error(self, publisher, identifier, message, code="io"):
        require(isinstance(message, str) and isinstance(code, str), "Invalid sync diagnostic")
        diagnostic = {"code": code, "message": message}
        with self.backend.lock():
            row = self.load(publisher, identifier)
            if row["sync_error"] == diagnostic:
                return self.status(publisher, identifier)
            return self.commit({**row, "sync_error": diagnostic}, row)

    def history(self, publisher, identifier):
        row = self.load(publisher, identifier)
        result, seen = [], set()
        while row is not None:
            require(row["revision"] not in seen, "Managed history cycle", "integrity")
            seen.add(row["revision"])
            result.append(row)
            row = self.load(publisher, identifier, row["previous"]) if row["previous"] else None
        return result

    def catalog(self, *, verify=True):
        result = []
        for publisher, identifier in self.backend.registrations():
            try:
                row = self.status(publisher, identifier)
                if verify:
                    self.backend.snapshot(row["active_digest"])
                result.append(row)
            except self.backend.errors as error:
                result.append(
                    {
                        "publisher_id": publisher,
                        "id": identifier,
                        "error": error.to_dict()
                        if hasattr(error, "to_dict")
                        else {"message": str(error)},
                    }
                )
        return result
