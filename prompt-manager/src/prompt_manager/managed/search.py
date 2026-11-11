"""Deterministic metadata search without reading content objects or writing indexes."""

from ..codec import canonical
from ..errors import diagnostic, require
from .metadata import normalize_tags


def search(
    state,
    query="",
    *,
    publisher_id=None,
    tags=(),
    archived=False,
    customized=None,
    reconciliation_required=None,
):
    require(isinstance(query, str), "Search query must be text")
    canonical(query)
    if publisher_id is not None:
        state.backend.identifier(publisher_id)
    for flag in (archived, customized, reconciliation_required):
        require(
            flag is None or type(flag) is bool, "Search status filters must be booleans or null"
        )
    requested = set(normalize_tags(tags))
    terms = query.casefold().split()
    items, issues, missing = [], [], []
    for publisher, identifier in state.backend.registrations(publisher=publisher_id):
        if publisher_id is not None and publisher != publisher_id:
            continue
        try:
            row = state.status(publisher, identifier)
        except state.backend.errors as error:
            issues.append(
                {"publisher_id": publisher, "package_id": identifier, "error": diagnostic(error)}
            )
            continue
        flags = (
            (archived, row.get("archived", False)),
            (customized, row["customized"]),
            (reconciliation_required, row["reconciliation_required"]),
        )
        if any(expected is not None and expected != actual for expected, actual in flags):
            continue
        if not requested <= set(row.get("tags", [])):
            continue
        summary = row.get("content_metadata")
        if summary is None:
            missing.append(
                {
                    "publisher_id": publisher,
                    "package_id": identifier,
                    "revision": row["revision"],
                    "active_digest": row["active_digest"],
                }
            )
        item = {
            "publisher_id": publisher,
            "package_id": identifier,
            "revision": row["revision"],
            "active_digest": row["active_digest"],
            "label": row.get("label", identifier),
            "pack_name": summary["name"] if summary else None,
            "description": summary["description"] if summary else None,
            "tags": list(row.get("tags", [])),
            "archived": row.get("archived", False),
            "customized": row["customized"],
            "reconciliation_required": row["reconciliation_required"],
            "tracking": row["tracking"],
            "metadata_complete": summary is not None,
        }
        fields = [
            publisher,
            identifier,
            item["label"],
            item["pack_name"] or "",
            item["description"] or "",
            *item["tags"],
        ]
        fields = [value.casefold() for value in fields]
        if all(any(term in field for field in fields) for term in terms):
            items.append(item)
    return {
        "schema": "prompt-manager/managed-search-1",
        "store": str(state.backend.home),
        "query": query,
        "items": items,
        "issues": issues,
        "metadata_missing": missing,
        "metadata_complete": not missing,
        "complete": not missing and not issues,
    }
