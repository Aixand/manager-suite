"""Three-way comparisons, conflict choices, and explicit adoption."""

from copy import deepcopy
from difflib import SequenceMatcher

from ..codec import digest
from ..errors import require

MISSING = object()


def same(left, right):
    if left is MISSING or right is MISSING:
        return left is right
    return digest(left) == digest(right)


def pointer(path, key):
    return path + "/" + key.replace("~", "~0").replace("/", "~1")


def side(value):
    return {"present": False} if value is MISSING else {"present": True, "value": value}


def merge_text(base, local, incoming):
    """Merge disjoint line edits; overlapping/boundary edits need review."""
    lines = base.splitlines(keepends=True)
    edits = []
    for text in (local, incoming):
        changed = text.splitlines(keepends=True)
        current = [
            (a, b, changed[c:d])
            for op, a, b, c, d in SequenceMatcher(None, lines, changed).get_opcodes()
            if op != "equal"
        ]
        for edit in current:
            if edit in edits:
                continue
            a, b, _ = edit
            for x, y, _ in edits:
                if max(a, x) < min(b, y) or (a == b and x <= a <= y) or (x == y and a <= x <= b):
                    return None
            edits.append(edit)
    for a, b, replacement in sorted(edits, reverse=True):
        lines[a:b] = replacement
    return "".join(lines)


def merge(base, local, incoming, resolutions=None, *, baseline_known=True, atomic_paths=()):
    resolutions = {} if resolutions is None else resolutions
    require(isinstance(resolutions, dict), "Resolutions must map conflict paths to choices")
    atomic_paths = frozenset(atomic_paths)
    conflicts, used = [], set()

    def walk(before, ours, theirs, path):
        if same(ours, theirs) or (baseline_known and same(before, theirs)):
            return ours
        if baseline_known and same(before, ours):
            return theirs
        # Captured dependency snapshots and their digests must stay together.
        dependency = path.startswith("/dependencies/") and path.count("/") == 2
        atomic = dependency or path in atomic_paths
        if (
            isinstance(ours, dict)
            and isinstance(theirs, dict)
            and (isinstance(before, dict) or not baseline_known)
            and not atomic
        ):
            before = before if isinstance(before, dict) else {}
            result = {}
            for key in sorted(before.keys() | ours.keys() | theirs.keys()):
                value = walk(
                    before.get(key, MISSING),
                    ours.get(key, MISSING),
                    theirs.get(key, MISSING),
                    pointer(path, key),
                )
                if value is not MISSING:
                    result[key] = value
            return result
        if (
            not atomic
            and baseline_known
            and all(isinstance(value, str) for value in (before, ours, theirs))
        ):
            text = merge_text(before, ours, theirs)
            if text is not None:
                return text
        if path in resolutions:
            used.add(path)
            choice = resolutions[path]
            require(isinstance(choice, dict), "Each conflict choice must be an object")
            if set(choice) == {"take"}:
                require(
                    isinstance(choice["take"], str)
                    and choice["take"] in {"managed", "publisher", "baseline"},
                    "Invalid conflict choice",
                )
                return {"managed": ours, "publisher": theirs, "baseline": before}[choice["take"]]
            if choice == {"delete": True}:
                return MISSING
            require(set(choice) == {"value"}, "Use take, delete, or value for a conflict")
            return choice["value"]
        conflicts.append(
            {
                "path": path,
                "baseline": side(before) if baseline_known else {"present": False},
                "managed": side(ours),
                "publisher": side(theirs),
            }
        )
        return ours

    candidate = walk(base, local, incoming, "")
    require(
        set(resolutions) == used,
        "Resolution contains a path that is not a current conflict",
        "conflict",
    )
    return deepcopy(candidate), conflicts


def changes(before, after, path=""):
    if same(before, after):
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        return [
            change
            for key in sorted(before.keys() | after.keys())
            for change in changes(
                before.get(key, MISSING), after.get(key, MISSING), pointer(path, key)
            )
        ]
    return [{"path": path, "before": side(before), "after": side(after)}]


def preview(state, publisher, identifier, resolutions=None, take=None):
    row = state.load(publisher, identifier)
    require(
        row["upstream"]["digest"] != row["baseline"]["digest"] or bool(row.get("source_review")),
        "No pending update",
        "conflict",
    )
    trees = {
        key: state.backend.tree(state.backend.snapshot(identity))
        for key, identity in {
            "baseline": row["baseline"]["digest"],
            "managed": row["active_digest"],
            "publisher": row["upstream"]["digest"],
        }.items()
    }
    if take is not None:
        require(
            isinstance(take, str) and take in {"managed", "publisher"} and not resolutions,
            "Choose take or conflict resolutions",
        )
        candidate, conflicts = trees[take], []
    else:
        candidate, conflicts = merge(
            trees["baseline"],
            trees["managed"],
            trees["publisher"],
            resolutions,
            baseline_known=not bool(row.get("migration")),
            atomic_paths={
                path for tree in trees.values() for path in state.backend.atomic_paths(tree)
            },
        )
    diagnostic = None
    try:
        state.backend.from_tree(candidate)
    except state.backend.errors as error:
        diagnostic = (
            error.to_dict()
            if hasattr(error, "to_dict")
            else {"code": "validation", "message": str(error)}
        )
    return {
        "revision": row["revision"],
        "baseline_known": not bool(row.get("migration")),
        "candidate": candidate,
        "conflicts": conflicts,
        "valid": diagnostic is None,
        "validation_error": diagnostic,
        "managed_changes": changes(trees["baseline"], trees["managed"]),
        "publisher_changes": changes(trees["baseline"], trees["publisher"]),
    }


def reconcile(state, publisher, identifier, expected_revision, resolutions=None, take=None):
    with state.backend.lock():
        row = state.guarded(publisher, identifier, expected_revision)
        preview = state.preview(publisher, identifier, resolutions, take)
        require(not preview["conflicts"], "Resolve all conflicts before applying", "conflict")
        snapshot = state.backend.from_tree(preview["candidate"])
        state.backend.store(snapshot)
        return state.commit(
            {
                **row,
                "active_digest": state.backend.snapshot_digest(snapshot),
                "baseline": row["upstream"],
                **({"source_review": False} if "source_review" in row else {}),
                **({"hold": False} if "hold" in row else {}),
                **({"migration": None} if row.get("migration") else {}),
            },
            row,
            snapshot=snapshot,
        )
