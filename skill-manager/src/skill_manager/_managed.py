"""Compatibility adapter over Prompt Manager's public package lifecycle SDK.

The private method names also serve Skill Manager's native acquisition adapter.
All accepted-state policy belongs to Prompt Manager.
"""

from prompt_manager import PackageLifecycle, PromptError

from .errors import SkillError, require


class Managed:
    def __init__(self, backend):
        self.backend = backend
        self.lifecycle = PackageLifecycle(backend)

    def _call(self, name, *args, **kwargs):
        try:
            return getattr(self.lifecycle, name)(*args, **kwargs)
        except PromptError as error:
            # Retain the existing Skill Manager filesystem-error boundary.
            if isinstance(error.__cause__, OSError):
                raise error.__cause__
            raise SkillError(error.code, str(error)) from error

    def path(self, publisher, identifier):
        self._call("validate_identity", publisher, identifier)
        return self.backend.home / "managed" / publisher / (identifier + ".json")

    def publisher_metadata(self, revision, version):
        return self._call("validate_publisher_metadata", revision, version)

    def load(self, publisher, identifier, revision=None, missing_ok=False):
        return self._call(
            "record", publisher, identifier, revision=revision, missing_ok=missing_ok
        )

    def guarded(self, publisher, identifier, expected_revision):
        row = self.load(publisher, identifier)
        require(
            row["revision"] == expected_revision,
            "Managed content or update changed; refresh before applying",
            "conflict",
        )
        return row

    def commit(self, row, previous):
        return self._call(
            "commit_record",
            row,
            expected_revision=previous["revision"] if previous else None,
        )

    def status(self, publisher, identifier, revision=None):
        return self._call("status", publisher, identifier, revision=revision)

    def ensure(
        self, publisher, identifier, source, publisher_revision=None, version=None
    ):
        return self._call(
            "ensure_registered",
            publisher,
            identifier,
            source,
            publisher_revision=publisher_revision,
            version=version,
        )

    def register(self, publisher, identifier, source, *, publisher_revision=None, version=None):
        return self._call("register", publisher, identifier, source,
                          publisher_revision=publisher_revision, version=version)

    def validate(self, publisher, identifier, files=None, *, revision=None):
        return self._call("validate", publisher, identifier, files, revision=revision)

    def compare(self, publisher, identifier, before_revision, after_revision=None):
        return self._call("compare", publisher, identifier, before_revision, after_revision)

    def adopt(self, publisher, identifier, legacy_source, publisher_source, **options):
        return self._call(
            "adopt_legacy",
            publisher,
            identifier,
            legacy_source,
            publisher_source,
            **options,
        )

    def edit(self, publisher, identifier, files, expected_revision):
        return self._call(
            "save", publisher, identifier, files, expected_revision=expected_revision
        )

    def preview(self, publisher, identifier, resolutions=None, take=None):
        return self._call(
            "preview_reconciliation",
            publisher,
            identifier,
            resolutions=resolutions,
            take=take,
        )

    def view(self, publisher, identifier, **options):
        return self._call("view", publisher, identifier, **options)

    def reconcile(
        self, publisher, identifier, expected_revision, resolutions=None, take=None
    ):
        return self._call(
            "reconcile",
            publisher,
            identifier,
            expected_revision=expected_revision,
            resolutions=resolutions,
            take=take,
        )

    def rollback(self, publisher, identifier, revision, expected_revision):
        return self._call(
            "rollback",
            publisher,
            identifier,
            revision,
            expected_revision=expected_revision,
        )

    def set_tracking(self, publisher, identifier, enabled, expected_revision):
        return self._call(
            "set_tracking",
            publisher,
            identifier,
            enabled,
            expected_revision=expected_revision,
        )

    def report_error(self, publisher, identifier, message, code="io"):
        return self._call(
            "report_sync_error", publisher, identifier, message, code=code
        )

    def history(self, publisher, identifier):
        return self._call("history", publisher, identifier)

    def catalog(self, *, verify=True):
        return self._call("catalog", verify=verify)

    def release(self, publisher, identifier, version, *, revision=None):
        return self._call("release", publisher, identifier, version, revision=revision)

    def export(self, publisher, identifier, destination, *, revision=None):
        return self._call(
            "export", publisher, identifier, destination, revision=revision
        )


class ManagedMethods:
    """Skill-facing operations delegated to the shared package lifecycle."""

    def _managed(self):
        from .managed import engine

        return engine(self)

    def managed_status(self, publisher, identifier, *, revision=None):
        return self._managed().status(publisher, identifier, revision)

    def managed_catalog(self):
        return self._managed().catalog()

    def managed_summaries(self):
        return self._managed().catalog(verify=False)

    def report_managed_sync_error(self, publisher, identifier, message, *, code="io"):
        return self._managed().report_error(publisher, identifier, message, code)

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
        return self._managed().adopt(
            publisher,
            identifier,
            legacy_source,
            publisher_source,
            publisher_revision=publisher_revision,
            version=version,
            expected_revision=expected_revision,
        )

    def managed_view(self, publisher, identifier, **options):
        return self._managed().view(publisher, identifier, **options)

    def managed_history(self, publisher, identifier):
        return self._managed().history(publisher, identifier)

    def save_managed(self, publisher, identifier, files, *, expected_revision):
        return self._managed().edit(publisher, identifier, files, expected_revision)

    def preview_reconciliation(
        self, publisher, identifier, *, resolutions=None, take=None
    ):
        return self._managed().preview(publisher, identifier, resolutions, take)

    def reconcile(
        self, publisher, identifier, *, expected_revision, resolutions=None, take=None
    ):
        return self._managed().reconcile(
            publisher, identifier, expected_revision, resolutions, take
        )

    def rollback_managed(self, publisher, identifier, revision, *, expected_revision):
        return self._managed().rollback(
            publisher, identifier, revision, expected_revision
        )

    def set_managed_tracking(
        self, publisher, identifier, enabled, *, expected_revision
    ):
        return self._managed().set_tracking(
            publisher, identifier, enabled, expected_revision
        )
