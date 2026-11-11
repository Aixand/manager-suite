"""Owned publisher lifecycle and managed selection resolution."""
from ..storage.codec import digest
from .backend import Backend
from .reconciliation import Managed


def engine(manager):
    return Managed(Backend(manager))


class ManagedService:
    """Composed lifecycle service; owns no agent or client session state."""

    def __init__(self, manager):
        self.manager = manager
        self.engine = engine(manager)

    def managed_status(self, publisher, identifier, *, revision=None):
        return self.engine.status(publisher, identifier, revision)

    def managed_catalog(self):
        return self.engine.catalog()

    def managed_summaries(self):
        return self.engine.catalog(verify=False)

    def report_managed_sync_error(self, publisher, identifier, message, *, code="io"):
        return self.engine.report_error(publisher, identifier, message, code)

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
        return self.engine.adopt(
            publisher,
            identifier,
            legacy_source,
            publisher_source,
            publisher_revision=publisher_revision,
            version=version,
            expected_revision=expected_revision,
        )

    def managed_view(self, publisher, identifier, **options):
        from .views import view

        return view(self.engine, publisher, identifier, **options)

    def managed_history(self, publisher, identifier):
        return self.engine.history(publisher, identifier)

    def save_managed(self, publisher, identifier, files, *, expected_revision):
        return self.engine.edit(publisher, identifier, files, expected_revision)

    def preview_reconciliation(self, publisher, identifier, *, resolutions=None, take=None):
        return self.engine.preview(publisher, identifier, resolutions, take)

    def reconcile(self, publisher, identifier, *, expected_revision, resolutions=None, take=None):
        return self.engine.reconcile(
            publisher, identifier, expected_revision, resolutions, take
        )

    def rollback_managed(self, publisher, identifier, revision, *, expected_revision):
        return self.engine.rollback(publisher, identifier, revision, expected_revision)

    def set_managed_tracking(self, publisher, identifier, enabled, *, expected_revision):
        return self.engine.set_tracking(publisher, identifier, enabled, expected_revision)

    def ensure_registered(self, publisher, definition, *, server_id="default", publisher_revision=None, version=None):
        return self.engine.ensure(publisher, server_id, definition, publisher_revision, version)

    def resolve_managed(self, publisher, server_id="default", *, revision=None):
        managed = self.engine
        status = managed.status(publisher, server_id, revision)
        snapshot = managed.backend.snapshot(status["active_digest"])
        return {"schema": "mcp-manager/managed-resolution-1", "definition": snapshot["definition"],
            "digest": status["active_digest"], "resolution": status, "store": str(self.manager.home), "store_id": self.manager.store_id}

    def inspect_managed(self, publisher, server_id, cwd, *, revision=None, **options):
        value = self.resolve_managed(publisher, server_id, revision=revision)
        from ..installations.service import resolve_definition
        definition, runtime = resolve_definition(self.manager.home, value['definition'])
        key = definition["kind"] if definition["kind"] != "stdio" else "definition"
        namespace = self.manager.home / "managed-preparation" / publisher / server_id
        result = self.manager.resolve([key], cwd, _definitions={key: definition}, _preparation_root=namespace, **options)
        identifier = "managed_" + digest([self.manager.store_id, publisher, server_id])[7:31]
        result["servers"] = {identifier: {**row, "server_name": "agent_manager_" + identifier}
                             for row in result["servers"].values()}
        result["states"] = [{**row, "name": identifier, "server_name": "agent_manager_" + identifier,
                             "managed": value["resolution"]} for row in result["states"]]
        if runtime:
            for state in result['states']:
                state['generation'] = runtime['generation']
                state['installation_digest'] = runtime['digest']
        result["resolution"] = value["resolution"]
        result["digest"] = digest({k: v for k, v in result.items() if k != "digest"})
        return result

    def prepare_managed(self, publisher, server_id, project, *, revision=None, refresh=False, settings=None, key=None):
        from ..jobs.service import start
        self.engine.path(publisher, server_id)
        if revision is not None:
            self.engine.backend.check_digest(revision)
        return start(self.manager, [], project, refresh=refresh, settings=settings, key=key,
            _managed={"publisher_id": publisher, "server_id": server_id, "revision": revision})

    def bootstrap_defaults(self):
        """Compatibility no-op: no server definitions are bundled with the manager."""
        return []
