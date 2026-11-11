"""Compose independent library, application, and managed catalog observations."""

from datetime import datetime, timezone

from ..applications.resolution import capture
from ..codec import digest
from ..errors import diagnostic
from ..managed.inspection import catalog as managed_catalog
from .discovery import entries


def catalog(store, packs, registry, managed, *, include_archived=False):
    rows, statuses = entries(store, packs, registry)
    store_id = digest(str(registry.home))[7:]
    applications = []
    for application in registry.list("applications"):
        resolutions = {}
        for logical in application.get("prompts", {}):
            try:
                resolutions[logical] = capture(
                    store, packs, registry, managed, application["id"], logical
                )[0]
            except (ValueError, OSError) as error:
                resolutions[logical] = {"error": diagnostic(error)}
        applications.append({**application, "resolutions": resolutions})
    return {
        "store": str(registry.home),
        "store_id": store_id,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "sources": statuses,
        "applications": applications,
        "packs": rows,
        "managed": managed_catalog(managed, include_archived=include_archived),
    }
