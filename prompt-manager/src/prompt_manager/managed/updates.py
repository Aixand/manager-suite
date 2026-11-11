"""Publisher update policy, tracking, and synchronization diagnostics."""

from ..errors import require


def ensure_content(
    state, publisher, identifier, source, revision, version, previous, *, snapshot=None
):
    if previous and not previous["tracking"]:
        return {**state.status(publisher, identifier), "outcome": "tracking_stopped"}
    before_registration = getattr(state.backend, "before_registration", None)
    if before_registration is not None:
        before_registration(previous)
    old_revision = previous["upstream"]["revision"] if previous else None
    if old_revision is not None:
        require(
            revision is not None,
            "An ordered publisher must supply publisher_revision",
            "conflict",
        )
        if revision < old_revision:
            return {**state.status(publisher, identifier), "outcome": "ignored_older"}
    if snapshot is None:
        snapshot = state.backend.capture(source)
    identity = state.backend.snapshot_digest(snapshot)
    incoming = {"digest": identity, "revision": revision, "version": version}
    if previous and revision is not None and revision == old_revision:
        require(
            identity == previous["upstream"]["digest"],
            "Publisher revision was reused with different content",
            "conflict",
        )
    state.backend.store(snapshot)
    if previous is None:
        row = {
            "schema": state.backend.schema,
            "publisher_id": publisher,
            "id": identifier,
            "baseline": incoming,
            "upstream": incoming,
            "active_digest": identity,
            "sync_error": None,
            "tracking": True,
        }
        return {**state.commit(row, None, snapshot=snapshot), "outcome": "imported"}
    row = {**previous, "upstream": incoming, "sync_error": None}
    outcome = "unchanged"
    if identity == previous["upstream"]["digest"]:
        if identity == previous["baseline"]["digest"]:
            row["baseline"] = incoming
    elif identity in {previous["active_digest"], previous["baseline"]["digest"]} or (
        revision is not None
        and not previous.get("hold")
        and not previous.get("migration")
        and previous["active_digest"] == previous["baseline"]["digest"]
    ):
        if (
            not previous.get("migration")
            and previous["active_digest"] == previous["baseline"]["digest"]
        ):
            row["active_digest"] = identity
        row["baseline"] = incoming
        outcome = "updated"
    else:
        outcome = "update_pending"
    if (
        row.get("migration")
        and identity == row["active_digest"]
        and row["baseline"]["digest"] == identity
    ):
        row["migration"] = None
    if all(row[key] == previous[key] for key in row if key != "revision"):
        return {**state.status(publisher, identifier), "outcome": outcome}
    return {
        **state.commit(
            row, previous, snapshot=snapshot if row["active_digest"] == identity else None
        ),
        "outcome": outcome,
    }


def set_tracking(state, publisher, identifier, enabled, expected_revision):
    require(type(enabled) is bool, "tracking must be a boolean")
    with state.backend.lock():
        row = state.guarded(publisher, identifier, expected_revision)
        if row["tracking"] == enabled:
            return state.status(publisher, identifier)
        return state.commit({**row, "tracking": enabled}, row)


def report_error(state, publisher, identifier, message, code="io"):
    require(isinstance(message, str) and isinstance(code, str), "Invalid sync diagnostic")
    diagnostic = {"code": code, "message": message}
    with state.backend.lock():
        row = state.load(publisher, identifier)
        if row["sync_error"] == diagnostic:
            return state.status(publisher, identifier)
        return state.commit({**row, "sync_error": diagnostic}, row)
