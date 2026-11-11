"""Owned native skill packages and consumer application handles."""

import base64

from . import fs
from ._managed import Managed
from .codec import DIGEST, byte_digest, canonical, digest, source_id
from .errors import SkillError, require
from .metadata import parse
from .store import json_read


class Backend:
    schema = "skill-manager/managed-1"
    errors = (SkillError, OSError)
    # Preserve the closed native state schema, including compatibility with old readers.
    store_content_metadata = False

    def __init__(self, manager):
        self.manager, self.home = manager, manager.home

    def identifier(self, value):
        source_id(value)

    def check_digest(self, value):
        require(
            isinstance(value, str) and DIGEST.fullmatch(value),
            "Invalid managed digest",
            "integrity",
        )

    def lock(self):
        return self.manager._store.lock()

    def read(self, path, *, missing_ok=False):
        try:
            row = json_read(path)
            self.validate_record(row)
            return row
        except FileNotFoundError:
            require(missing_ok, f"Managed registration not found: {path}", "not_found")
            return None

    def write(self, path, row, *, immutable=False):
        self.validate_record(row)
        self.manager._store.put_json(path, row, immutable=immutable)

    def validate_record(self, row):
        if isinstance(row, dict) and "source" in row:
            from ._source_records import SourceError, validate_source
            try:
                validate_source(row["source"])
            except SourceError as error:
                raise SkillError("integrity", str(error)) from error

    def before_registration(self, previous):
        require(not previous or not previous.get("source"),
                "GitHub resources must use check_updates", "conflict")

    def describe(self, snapshot, identity):
        manifest = snapshot["manifest"]
        return {"digest": identity, "name": manifest["name"],
                "description": manifest["metadata"]["description"]}

    def atomic_paths(self, tree):
        return ["/files/" + path.replace("~", "~0").replace("/", "~1") + "/content_b64"
                for path, value in tree["files"].items() if "content_b64" in value]

    def publish(self, snapshot, version, status):
        resolved = resolved_snapshot(self.manager, status, snapshot)
        return self.manager._publish_managed(resolved, status["publisher_id"], status["id"], version)

    def export(self, snapshot, destination, status):
        return self.manager._export_resolved(
            resolved_snapshot(self.manager, status, snapshot), destination, locked=True)

    def registrations(self):
        rows = []
        try:
            with fs.directory(self.home / "managed") as fd:
                import os

                publishers = sorted(os.listdir(fd))
        except FileNotFoundError:
            return rows
        for publisher in publishers:
            self.identifier(publisher)
            with fs.directory(self.home / "managed" / publisher) as fd:
                rows.extend(
                    (publisher, filename[:-5])
                    for filename in sorted(os.listdir(fd))
                    if filename.endswith(".json")
                )
        return rows

    def capture(self, source):
        snapshot = fs.capture(source)
        require(
            snapshot["error"] is None,
            f"Import requires a valid skill: {snapshot['error']}",
        )
        require(
            fs.capture(source)["digest"] == snapshot["digest"],
            "Publisher changed during import",
            "conflict",
        )
        return snapshot

    def snapshot_digest(self, snapshot):
        return snapshot["digest"]

    def store(self, snapshot):
        self.manager._store.capture(snapshot)

    def snapshot(self, identity):
        self.check_digest(identity)
        record = json_read(self.manager._store.object_path(identity))
        require(
            isinstance(record, dict) and isinstance(record.get("name"), str),
            "Invalid managed manifest",
            "integrity",
        )
        manifest = self.manager._manifest(identity, record["name"])
        return {
            "digest": identity,
            "manifest": manifest,
            "error": None,
            "contents": {
                row["path"]: self.manager._read_blob(row) for row in manifest["files"]
            },
        }

    def tree(self, snapshot):
        files = {}
        for row in snapshot["manifest"]["files"]:
            content = snapshot["contents"][row["path"]]
            try:
                text = content.decode("utf-8")
                require("\0" not in text, "Binary data")
                value = {"text": text}
            except (UnicodeError, SkillError):
                value = {"content_b64": base64.b64encode(content).decode("ascii")}
            files[row["path"]] = {**value, "executable": row["executable"]}
        return {
            "name": snapshot["manifest"]["name"],
            "files": files,
            "directories": dict.fromkeys(snapshot["manifest"]["directories"], True),
        }

    def from_tree(self, tree):
        canonical(tree)
        require(
            isinstance(tree, dict) and set(tree) == {"name", "files", "directories"},
            "Invalid managed skill tree",
        )
        require(
            isinstance(tree["files"], dict) and isinstance(tree["directories"], dict),
            "Invalid skill files/directories",
        )
        contents, files, total = {}, [], 0
        directories = set(tree["directories"])
        require(
            all(value is True for value in tree["directories"].values()),
            "Directory entries must be true",
        )
        for path in list(directories):
            parts = fs.relative(path)
            directories.update("/".join(parts[:i]) for i in range(1, len(parts)))
        for path, value in sorted(tree["files"].items()):
            parts = fs.relative(path)
            require(
                isinstance(value, dict)
                and set(value)
                in ({"text", "executable"}, {"content_b64", "executable"})
                and type(value["executable"]) is bool,
                "Each file needs text or content_b64 and executable",
            )
            if "text" in value:
                require(isinstance(value["text"], str), "File text must be a string")
                try:
                    content = value["text"].encode("utf-8")
                except UnicodeError as error:
                    raise SkillError(
                        "validation", "File text must be valid UTF-8"
                    ) from error
            else:
                require(
                    isinstance(value["content_b64"], str), "content_b64 must be text"
                )
                try:
                    content = base64.b64decode(value["content_b64"], validate=True)
                except ValueError as error:
                    raise SkillError(
                        "validation", "Invalid binary file encoding"
                    ) from error
            require(len(content) <= fs.MAX_FILE, "File exceeds size limit")
            total += len(content)
            contents[path] = content
            directories.update("/".join(parts[:i]) for i in range(1, len(parts)))
            files.append(
                {
                    "path": path,
                    "size": len(content),
                    "digest": byte_digest(content),
                    "executable": value["executable"],
                }
            )
        require(
            total <= fs.MAX_TOTAL and len(files) + len(directories) <= fs.MAX_ENTRIES,
            "Package exceeds limits",
        )
        fs.check_paths(sorted(directories), contents)
        require("SKILL.md" in contents, "A managed skill requires SKILL.md")
        manifest = {
            "schema": "skill-manager/package-1",
            "name": tree["name"],
            "metadata": parse(contents["SKILL.md"], tree["name"]),
            "directories": sorted(directories),
            "files": files,
        }
        return {
            "manifest": manifest,
            "contents": contents,
            "digest": digest(manifest),
            "error": None,
        }

    def edit(self, snapshot, changes):
        require(
            isinstance(changes, dict),
            "Files must map paths to file specifications or null",
        )
        tree = self.tree(snapshot)
        for path, value in changes.items():
            fs.relative(path)
            if value is None:
                tree["files"].pop(path, None)
                continue
            require(
                isinstance(value, dict)
                and set(value) == {"content", "executable"}
                and isinstance(value["content"], bytes)
                and type(value["executable"]) is bool,
                "Each file needs bytes content and bool executable",
            )
            tree["files"][path] = {
                "content_b64": base64.b64encode(value["content"]).decode("ascii"),
                "executable": value["executable"],
            }
        return self.from_tree(tree)


class Application:
    def __init__(
        self,
        manager,
        publisher,
        bundled_skills,
        *,
        publisher_revision=None,
        version=None,
    ):
        require(
            isinstance(bundled_skills, dict),
            "bundled_skills must map stable skill IDs to paths",
        )
        self.manager, self.publisher = manager, publisher
        self.paths = bundled_skills
        self.publisher_revision, self.version = publisher_revision, version
        managed = engine(manager)
        managed.publisher_metadata(publisher_revision, version)
        for identifier in bundled_skills:
            managed.path(publisher, identifier)
        self.refresh(allow_existing=True)

    def refresh(self, *, allow_existing=False):
        result = []
        for identifier, path in self.paths.items():
            try:
                result.append(
                    self.manager.ensure_registered(
                        self.publisher,
                        path,
                        skill_id=identifier,
                        publisher_revision=self.publisher_revision,
                        version=self.version,
                    )
                )
            except (SkillError, OSError):
                if not allow_existing:
                    raise
                self.manager.resolve_managed(self.publisher, identifier)
                result.append(self.manager.managed_status(self.publisher, identifier))
        return result

    def status(self):
        return [
            row
            for row in self.manager.managed_catalog()
            if row["publisher_id"] == self.publisher
        ]

    def resolve(self, skill, *, revision=None):
        return self.manager.resolve_managed(self.publisher, skill, revision=revision)

    def export(self, skill, destination, *, revision=None):
        return self.manager.export_managed(
            self.publisher, skill, destination, revision=revision
        )


def engine(manager):
    return Managed(Backend(manager))


def resolve_managed(manager, publisher, identifier, *, revision=None):
    managed = engine(manager)
    status = managed.status(publisher, identifier, revision)
    snapshot = managed.backend.snapshot(status["active_digest"])
    return resolved_snapshot(manager, status, snapshot)


def resolved_snapshot(manager, status, snapshot):
    publisher, identifier = status["publisher_id"], status["id"]
    manifest = snapshot["manifest"]
    return {
        "schema": "skill-manager/managed-resolution-1",
        "store": str(manager.home),
        "store_id": manager.store_id,
        "source_id": publisher,
        "name": manifest["name"],
        "reference": f"managed:{publisher}/{identifier}@{status['revision']}",
        "digest": snapshot["digest"],
        "manifest": manifest,
        "resolution": status,
        "object": str(manager._store.object_path(snapshot["digest"])),
        "blobs": {
            row["path"]: str(manager._store.blob_path(row["digest"]))
            for row in manifest["files"]
        },
    }
