"""A tiny independent binary-package adapter for public lifecycle contract tests.

This fixture is intentionally not a production filesystem importer or exporter.
It accepts already-captured trees, so it needs no skill parser or other project.
"""

import base64
from copy import deepcopy

from prompt_manager.codec import digest
from prompt_manager.errors import PromptError, require
from prompt_manager.storage.files import read_json
from prompt_manager.storage.locks import lock
from prompt_manager.storage.objects import load_object, store_object
from prompt_manager.storage.records import write_record


class FileBackend:
    schema = "example-files/managed-1"
    errors = (PromptError, OSError)

    def __init__(self, home):
        self.home = home

    def identifier(self, value):
        require(isinstance(value, str) and value.isalnum(), "Invalid identifier")

    def check_digest(self, value):
        require(
            isinstance(value, str)
            and value.startswith("sha256:")
            and len(value) == 71
            and all(c in "0123456789abcdef" for c in value[7:]),
            "Invalid digest",
            "integrity",
        )

    def lock(self):
        return lock(self.home, "managed")

    def read(self, path, *, missing_ok=False):
        if missing_ok and not path.exists():
            return None
        return read_json(path)

    def write(self, path, row, *, immutable=False):
        write_record(path, row, immutable=immutable)

    def registrations(self):
        return [
            (path.parent.name, path.stem)
            for path in sorted((self.home / "managed").glob("*/*.json"))
        ]

    def capture(self, source):
        return self.from_tree(source)

    def snapshot_digest(self, snapshot):
        return digest(snapshot)

    def store(self, snapshot):
        self.from_tree(snapshot)
        store_object(self.home, snapshot)

    def snapshot(self, identity):
        self.check_digest(identity)
        return self.from_tree(load_object(self.home, identity))

    def tree(self, snapshot):
        return deepcopy(snapshot)

    def from_tree(self, tree):
        require(
            isinstance(tree, dict) and set(tree) == {"name", "files", "directories"},
            "Invalid file package",
        )
        require(tree["name"] == "file-example", "Unexpected package name")
        require(
            isinstance(tree["directories"], dict)
            and all(value is True for value in tree["directories"].values()),
            "Invalid directories",
        )
        require(
            isinstance(tree["files"], dict) and "instructions.md" in tree["files"],
            "Instructions required",
        )
        for value in tree["files"].values():
            require(
                isinstance(value, dict)
                and set(value) in ({"text", "executable"}, {"content_b64", "executable"})
                and type(value["executable"]) is bool,
                "Invalid file",
            )
            if "text" in value:
                require(isinstance(value["text"], str), "Text required")
            else:
                try:
                    base64.b64decode(value["content_b64"], validate=True)
                except (ValueError, TypeError) as error:
                    raise PromptError("validation", "Invalid binary") from error
        return deepcopy(tree)

    def edit(self, snapshot, files):
        tree = deepcopy(snapshot)
        for path, value in files.items():
            if value is None:
                tree["files"].pop(path, None)
            else:
                tree["files"][path] = deepcopy(value)
        return self.from_tree(tree)

    def describe(self, snapshot, identity):
        return {"digest": identity, "name": snapshot["name"], "description": "Binary package"}

    def atomic_paths(self, tree):
        return [
            "/files/" + path.replace("~", "~0").replace("/", "~1") + "/content_b64"
            for path, value in tree["files"].items()
            if "content_b64" in value
        ]


def sample():
    return {
        "name": "file-example",
        "directories": {"empty": True},
        "files": {
            "instructions.md": {"text": "first\nmiddle\nlast\n", "executable": False},
            "image.bin": {"content_b64": "AP8=", "executable": False},
            ".hidden": {"text": "original\r\n", "executable": False},
            "run.py": {"text": "print('hello')\n", "executable": True},
        },
    }
