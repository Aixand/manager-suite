import contextlib
import os
from pathlib import Path

from . import fs
from ._paths import default_root
from .codec import canonical, digest, loads
from .errors import SkillError, require

MAX_JSON = 64 * 1024 * 1024


def json_read(path):
    try:
        return loads(fs.read_path(path, limit=MAX_JSON))
    except SkillError as exc:
        raise SkillError("integrity", f"Invalid stored JSON at {path}: {exc.message}") from exc


class Store:
    def __init__(self, home=None):
        selected = home if home is not None else os.environ.get("SKILL_MANAGER_HOME")
        if selected is None:
            selected = default_root()
        self.home = fs.absolute(selected).resolve()
        self.id = digest({"kind": "skill", "home": str(self.home)})

    @contextlib.contextmanager
    def lock(self):
        from aixand_filesystem import lock_file
        fs.make_directories(self.home / ".locks")
        with fs.directory(self.home / ".locks") as fd:
            if os.name == "nt":
                stream = (Path(fd) / "store.lock").open("a+")
            else:
                stream = os.fdopen(os.open("store.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=fd), "a+")
        with stream:
            lock_file(stream, blocking=True)
            yield

    def put(self, path, content, *, immutable=False):
        fs.make_directories(path.parent)
        fs.write_atomic(path, content, immutable=immutable)

    def put_json(self, path, value, *, immutable=False):
        content = canonical(value)
        require(len(content) <= MAX_JSON, "Store JSON exceeds the 64 MiB limit")
        self.put(path, content, immutable=immutable)

    def object_path(self, identity):
        return self.home / "objects" / (identity[7:] + ".json")

    def blob_path(self, identity):
        return self.home / "blobs" / identity[7:]

    def capture(self, snapshot):
        for row in snapshot["manifest"]["files"]:
            self.put(self.blob_path(row["digest"]), snapshot["contents"][row["path"]], immutable=True)
        self.put_json(self.object_path(snapshot["digest"]), snapshot["manifest"], immutable=True)
