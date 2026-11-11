"""Managed state integrity and coordination of owned prompt operations."""

from copy import deepcopy

from ..codec import digest
from ..errors import require
from . import editing, history, inspection, metadata, reconciliation, registration, updates


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
        return self.validate_record(row, publisher, identifier, revision)

    def validate_record(self, row, publisher, identifier, revision=None):
        """Validate a complete candidate before any managed pointer is published."""
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
            isinstance(row, dict)
            and keys
            <= set(row)
            <= keys
            | {
                "migration",
                "source",
                "source_review",
                "hold",
                "label",
                "archived",
                "tags",
                "content_metadata",
            },
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
        if "source" in row:
            require(isinstance(row["source"], dict), "Invalid legacy source metadata", "integrity")
        for flag in ("hold", "source_review", "archived"):
            require(
                flag not in row or type(row[flag]) is bool,
                "Invalid managed state flag",
                "integrity",
            )
        require(
            "label" not in row or isinstance(row["label"], str) and 0 < len(row["label"]) <= 128,
            "Invalid managed label",
            "integrity",
        )
        if "tags" in row:
            require(
                row["tags"] == metadata.normalize_tags(row["tags"], code="integrity"),
                "Managed tags must be normalized",
                "integrity",
            )
        if "content_metadata" in row:
            self.backend.validate_description(row["content_metadata"], row["active_digest"])
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
        pending = (
            bool(row.get("source_review")) or row["upstream"]["digest"] != row["baseline"]["digest"]
        )
        return {
            **row,
            "customized": bool(row.get("migration"))
            or bool(row.get("hold"))
            or row["active_digest"] != row["baseline"]["digest"],
            "baseline_known": not bool(row.get("migration")),
            "reconciliation_required": pending,
            "update_status": "reconciliation_required" if pending else "current",
        }

    def commit(self, row, previous, *, snapshot=None):
        if "source" in row:
            require(isinstance(row["source"], dict), "Invalid legacy source metadata", "integrity")
        row = deepcopy(row)
        row.pop("revision", None)
        if snapshot is not None:
            require(
                self.backend.snapshot_digest(snapshot) == row["active_digest"],
                "Content metadata must describe the active snapshot",
                "integrity",
            )
            if getattr(self.backend, "store_content_metadata", True):
                row["content_metadata"] = self.backend.describe(snapshot, row["active_digest"])
        if "content_metadata" in row:
            self.backend.validate_description(row["content_metadata"], row["active_digest"])

        row["sequence"] = previous["sequence"] + 1 if previous else 1
        row["previous"] = previous["revision"] if previous else None
        row["revision"] = digest(row)
        self.validate_record(row, row["publisher_id"], row["id"])
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
        return registration.ensure(self, publisher, identifier, source, publisher_revision, version)

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
        return registration.adopt(
            self,
            publisher,
            identifier,
            legacy_source,
            publisher_source,
            publisher_revision=publisher_revision,
            version=version,
            expected_revision=expected_revision,
        )

    def edit(self, publisher, identifier, files, expected_revision):
        return editing.save(self, publisher, identifier, files, expected_revision)

    def preview(self, publisher, identifier, resolutions=None, take=None):
        return reconciliation.preview(self, publisher, identifier, resolutions, take)

    def reconcile(self, publisher, identifier, expected_revision, resolutions=None, take=None):
        return reconciliation.reconcile(
            self, publisher, identifier, expected_revision, resolutions, take
        )

    def rollback(self, publisher, identifier, revision, expected_revision):
        return history.rollback(self, publisher, identifier, revision, expected_revision)

    def set_tracking(self, publisher, identifier, enabled, expected_revision):
        return updates.set_tracking(self, publisher, identifier, enabled, expected_revision)

    def report_error(self, publisher, identifier, message, code="io"):
        return updates.report_error(self, publisher, identifier, message, code)

    def history(self, publisher, identifier):
        return history.history(self, publisher, identifier)

    def catalog(self, *, verify=True, include_archived=False):
        return inspection.catalog(self, verify=verify, include_archived=include_archived)


def engine(store):
    return Managed(registration.Backend(store))
