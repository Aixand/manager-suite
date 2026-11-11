"""Public SDK facade, preserving existing definition and preparation contracts."""
from pathlib import Path
import os

from .errors import McpError
from ._paths import default_root
from .storage.records import read, write
from .library.definitions import AUTO, PREFIX, PRESETS, PROJECT_KINDS, definition, digest, names, preparation_digest
from .library import resolution
from .diagnostics.stdio import probe
from .library.service import ManagedService
from .sources.service import SourceService
from .library.registry import Registry, LEGACY as _LEGACY


class Manager:
    resource_kind = 'mcp'

    def __init__(self, home=None):
        if home is None:
            home = os.environ.get("MCP_MANAGER_HOME")
            if not home:
                home = default_root()
        self.root = Path(home).expanduser().resolve()
        self.home = self.root
        self.store_id = digest({"kind": "mcp", "home": str(self.root)})
        self._registry = Registry(self.root)
        self._library = ManagedService(self)
        self._source_service = SourceService(
            self.home, self._library.engine, source_client=lambda: getattr(self, "source_client", None))

    def registry(self):
        return self._registry.read()

    def capture_artifact(self, source, *, suffix=".pyz"):
        """Capture publisher code/data without installing or executing it."""
        from .library.artifacts import capture
        return capture(self, source, suffix=suffix)

    def configure(self, identifier, value, *, expected_revision=None):
        """Create an absent definition, or replace under its observed revision."""
        return self._configure(identifier, value, expected_revision=expected_revision)

    def add(self, identifier, value):
        """Create an explicitly supplied server definition only if its name is absent."""
        return self.configure(identifier, value, expected_revision=None)

    def _configure_legacy(self, identifier, value):
        """Compatibility-only unguarded path; never used by standalone configure."""
        return self._configure(identifier, value, expected_revision=_LEGACY, legacy=True)

    def _configure(self, identifier, value, *, expected_revision, legacy=False):
        return self._registry.configure(identifier, value, expected_revision=expected_revision, legacy=legacy)

    def catalog(self):
        return self._registry.catalog()

    def show(self, identifier):
        return self._registry.show(identifier)

    def project(self, root):
        return resolution.project_path(self, root)

    def context(self, cwd, project=AUTO):
        return resolution.context(self, cwd, project)

    def resolve(self, servers, cwd, *, project=AUTO, strict=False, check=False, _definitions=None, _preparation_root=None):
        return resolution.resolve(self, servers, cwd, project=project, strict=strict, check=check,
                                  _definitions=_definitions, _preparation_root=_preparation_root)

    def inspect(self, servers, cwd, *, project=AUTO):
        return resolution.inspect(self, servers, cwd, project=project)

    def status(self, project, *, servers=None, check=False):
        return resolution.status(self, project, servers=servers, check=check)

    def prepare(self, tools, project, *, refresh=False, settings=None, key=None):
        from .jobs.service import start
        return start(self, tools, project, refresh=refresh, settings=settings, key=key)

    def job(self, identifier):
        from .jobs.service import status
        return status(self.root, identifier)

    def jobs(self, project=None):
        from .jobs.service import listing
        return listing(self.root, project)

    def refresh(self, tools, project, *, settings=None, key=None):
        return self.prepare(tools, project, refresh=True, settings=settings, key=key)

    async def runtime_capabilities(self, connector, target):
        from .live.service import capabilities
        return await capabilities(connector, target)

    async def runtime_status(self, connector, target, *, scope="session", server_name=None):
        from .live.service import inventory
        return await inventory(connector, target, scope=scope, server_name=server_name)

    async def reconnect(self, connector, target, *, scope, operation_id,
                        server_name=None, expected_selection_digest=None):
        from .live.service import reconnect
        try:
            return await reconnect(self, connector, target, scope=scope, operation_id=operation_id,
                                   server_name=server_name, expected_selection_digest=expected_selection_digest)
        except McpError as error:
            if error.operation_id is None and error.code != "validation":
                raise McpError(error.code, error.message, uncertain=error.uncertain,
                               operation_id=operation_id, recovery=error.recovery or {"target": target}) from error
            raise

    def runtime_operation(self, operation_id):
        """Inspect the retained reconnect receipt without contacting the backend."""
        from .live.service import operation
        return operation(self, operation_id)

    def _managed(self):
        """Expose the managed engine; internal services use explicit dependencies."""
        return self._library.engine

    def managed_status(self, publisher, identifier, *, revision=None):
        return self._library.managed_status(publisher, identifier, revision=revision)

    def managed_catalog(self):
        return self._library.managed_catalog()

    def managed_summaries(self):
        return self._library.managed_summaries()

    def report_managed_sync_error(self, publisher, identifier, message, *, code='io'):
        return self._library.report_managed_sync_error(publisher, identifier, message, code=code)

    def adopt_legacy(self, publisher, identifier, legacy_source, publisher_source, *,
                     publisher_revision=None, version=None, expected_revision=None):
        return self._library.adopt_legacy(
            publisher, identifier, legacy_source, publisher_source,
            publisher_revision=publisher_revision, version=version, expected_revision=expected_revision)

    def managed_view(self, publisher, identifier, **options):
        return self._library.managed_view(publisher, identifier, **options)

    def managed_history(self, publisher, identifier):
        return self._library.managed_history(publisher, identifier)

    def save_managed(self, publisher, identifier, files, *, expected_revision):
        return self._library.save_managed(publisher, identifier, files, expected_revision=expected_revision)

    def preview_reconciliation(self, publisher, identifier, *, resolutions=None, take=None):
        return self._library.preview_reconciliation(publisher, identifier, resolutions=resolutions, take=take)

    def reconcile(self, publisher, identifier, *, expected_revision, resolutions=None, take=None):
        return self._library.reconcile(publisher, identifier, expected_revision=expected_revision, resolutions=resolutions, take=take)

    def rollback_managed(self, publisher, identifier, revision, *, expected_revision):
        return self._library.rollback_managed(publisher, identifier, revision, expected_revision=expected_revision)

    def set_managed_tracking(self, publisher, identifier, enabled, *, expected_revision):
        return self._library.set_managed_tracking(publisher, identifier, enabled, expected_revision=expected_revision)

    def ensure_registered(self, publisher, definition, *, server_id='default', publisher_revision=None, version=None):
        return self._library.ensure_registered(
            publisher, definition, server_id=server_id, publisher_revision=publisher_revision, version=version)

    def resolve_managed(self, publisher, server_id='default', *, revision=None):
        return self._library.resolve_managed(publisher, server_id, revision=revision)

    def inspect_managed(self, publisher, server_id, cwd, *, revision=None, **options):
        return self._library.inspect_managed(publisher, server_id, cwd, revision=revision, **options)

    def prepare_managed(self, publisher, server_id, project, *, revision=None, refresh=False, settings=None, key=None):
        return self._library.prepare_managed(publisher, server_id, project, revision=revision, refresh=refresh, settings=settings, key=key)

    def bootstrap_defaults(self):
        return self._library.bootstrap_defaults()

    def _sources(self):
        return self._source_service.backend()

    def _source_call(self, method, *args, **kwargs):
        return self._source_service.call(method, *args, **kwargs)

    def source_capabilities(self):
        return self._source_service.source_capabilities()

    def create_managed(self, name, **options):
        return self._source_service.create_managed(name, **options)

    def discover_source(self, url, **options):
        return self._source_service.discover_source(url, **options)

    def import_source(self, discovery_id, selections=None, **options):
        return self._source_service.import_source(discovery_id, selections, **options)

    def import_local(self, path, **options):
        return self._source_service.import_local(path, **options)

    def check_updates(self, publisher, identifier, **options):
        return self._source_service.check_updates(publisher, identifier, **options)

    def source_status(self, publisher, identifier):
        return self._source_service.source_status(publisher, identifier)

    def source_settings(self, publisher, identifier, **options):
        return self._source_service.source_settings(publisher, identifier, **options)

    def switch_source(self, publisher, identifier, **options):
        return self._source_service.switch_source(publisher, identifier, **options)

    def check_due_sources(self):
        return self._source_service.check_due_sources()

    def _job_call(self, method, *args, **kwargs):
        return self._source_service.job_call(method, *args, **kwargs)

    def start_source_job(self, operation, request, *, key=None):
        return self._source_service.start_source_job(operation, request, key=key)

    def source_job(self, job_id):
        return self._source_service.source_job(job_id)

    def source_jobs(self):
        return self._source_service.source_jobs()

    def cancel_source_job(self, job_id):
        return self._source_service.cancel_source_job(job_id)
