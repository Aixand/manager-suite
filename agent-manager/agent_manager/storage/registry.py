"""Raw registry JSON persistence; identity and schema rules belong to services."""
import json

from .files import atomic


def read_state(path):
    return json.loads(path.read_text())


def write_state(path, state):
    atomic(path, (json.dumps(state, indent=2) + "\n").encode())
