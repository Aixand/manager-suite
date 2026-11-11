"""Compatibility exports for the managed lifecycle and its public mixin."""
from .library.reconciliation import MISSING, Managed, changes, merge, pointer, same, side


class ManagedMethods:
    """Owned lifecycle operations exposed by independent manager adapters."""

    def _managed(self):
        from .library.service import engine

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
        from .library.views import view

        return view(self._managed(), publisher, identifier, **options)

    def managed_history(self, publisher, identifier):
        return self._managed().history(publisher, identifier)

    def save_managed(self, publisher, identifier, files, *, expected_revision):
        return self._managed().edit(publisher, identifier, files, expected_revision)

    def preview_reconciliation(self, publisher, identifier, *, resolutions=None, take=None):
        return self._managed().preview(publisher, identifier, resolutions, take)

    def reconcile(self, publisher, identifier, *, expected_revision, resolutions=None, take=None):
        return self._managed().reconcile(
            publisher, identifier, expected_revision, resolutions, take
        )

    def rollback_managed(self, publisher, identifier, revision, *, expected_revision):
        return self._managed().rollback(publisher, identifier, revision, expected_revision)

    def set_managed_tracking(self, publisher, identifier, enabled, *, expected_revision):
        return self._managed().set_tracking(publisher, identifier, enabled, expected_revision)
