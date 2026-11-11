"""Raw registry records and optimistic revision guards."""

from ..errors import require
from .files import atomic_json, read_json
from .objects import publish_immutable

UNSET = object()


def check_revision(current, expected_revision):
    require(
        (current["revision"] if current else None) == expected_revision,
        "Registration changed; refresh its revision before writing",
        "conflict",
    )


def read_record(home, kind, identifier, *, missing_ok=False):
    path = home / kind / (identifier + ".json")
    if not path.exists():
        require(missing_ok, f"Registration not found: {kind}/{identifier}", "not_found")
        return None
    return read_json(path)


def write_record(path, row, *, immutable=False):
    (publish_immutable if immutable else atomic_json)(path, row)
