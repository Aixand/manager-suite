"""Historical managed revisions and explicit restoration."""

from ..errors import require


def history(state, publisher, identifier):
    row = state.load(publisher, identifier)
    result, seen = [], set()
    while row is not None:
        require(row["revision"] not in seen, "Managed history cycle", "integrity")
        seen.add(row["revision"])
        result.append(row)
        row = state.load(publisher, identifier, row["previous"]) if row["previous"] else None
    return result


def rollback(state, publisher, identifier, revision, expected_revision):
    with state.backend.lock():
        row = state.guarded(publisher, identifier, expected_revision)
        historical = state.load(publisher, identifier, revision)
        snapshot = state.backend.snapshot(historical["active_digest"])
        return state.commit(
            {**row, "active_digest": historical["active_digest"], "hold": True},
            row,
            snapshot=snapshot,
        )
