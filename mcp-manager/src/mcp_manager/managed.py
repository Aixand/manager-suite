"""Compatibility exports; owned lifecycle implementation lives in library/."""
from ._managed import ManagedMethods
from .library.backend import Backend, DIGEST, IDENTIFIER, normalize
from .library.service import ManagedService, engine


class ManagedMcpMethods(ManagedMethods):
    """Public mixin for consumers; Manager uses composition."""

    def ensure_registered(self, publisher, definition, *, server_id='default', publisher_revision=None, version=None):
        return ManagedService(self).ensure_registered(publisher, definition, server_id=server_id, publisher_revision=publisher_revision, version=version)

    def resolve_managed(self, publisher, server_id='default', *, revision=None):
        return ManagedService(self).resolve_managed(publisher, server_id, revision=revision)

    def inspect_managed(self, publisher, server_id, cwd, *, revision=None, **options):
        return ManagedService(self).inspect_managed(publisher, server_id, cwd, revision=revision, **options)

    def prepare_managed(self, publisher, server_id, project, *, revision=None, refresh=False, settings=None, key=None):
        return ManagedService(self).prepare_managed(publisher, server_id, project, revision=revision, refresh=refresh, settings=settings, key=key)

    def bootstrap_defaults(self):
        return ManagedService(self).bootstrap_defaults()
