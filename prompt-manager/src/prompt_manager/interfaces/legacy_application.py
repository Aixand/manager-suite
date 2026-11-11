"""Convenient application handle over the public Manager operations."""

from ..errors import PromptError
from ..managed.state import engine


class Application:
    """Ensure bundled defaults once; render only the active owned snapshot.

    A failed refresh retains an existing usable registration and exposes its
    sync_error through status/render provenance. Initial import failures raise.
    """

    def __init__(
        self,
        manager,
        publisher,
        bundled_pack,
        *,
        package_id="default",
        publisher_revision=None,
        version=None,
    ):
        self.manager, self.publisher, self.package_id = manager, publisher, package_id
        self.path = bundled_pack
        self.publisher_revision, self.version = publisher_revision, version
        managed = engine(manager.store)
        managed.path(publisher, package_id)
        managed.publisher_metadata(publisher_revision, version)
        try:
            self.refresh()
        except (PromptError, OSError):
            manager.resolve_managed(publisher, package_id)

    def refresh(self):
        return self.manager.ensure_registered(
            self.publisher,
            self.path,
            package_id=self.package_id,
            publisher_revision=self.publisher_revision,
            version=self.version,
        )

    def status(self):
        return self.manager.managed_status(self.publisher, self.package_id)

    def render(self, prompt, inputs=None, *, format="text", revision=None):
        return self.manager.render_managed(
            self.publisher, self.package_id, prompt, inputs, format=format, revision=revision
        )
