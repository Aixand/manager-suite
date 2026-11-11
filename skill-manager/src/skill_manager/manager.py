"""Standalone synchronous API: JSON records and explicit typed SDK handles."""
import contextlib
from functools import wraps
import os
from pathlib import Path
import shutil
import stat
import secrets
from datetime import datetime, timezone

from .sources import SourceMethods

from . import fs
from ._managed import ManagedMethods
from .codec import DIGEST, NAME, SOURCE, VERSION, byte_digest, digest, reference as parse_reference, skill_name, source_id as validate_source_id
from .errors import SkillError, require
from .metadata import parse
from .store import Store, json_read
from .contracts import CollectionDiscovery, CollectionImport, ProjectConnections, ProjectOperation, ProjectPlan, ManagedStatus, ComparisonResult, FileChanges, SkillReference, ValidationResult, file_inputs
from .sdk import Project, RegistrationResult, ResolvedSkill, Skill


def operation(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except OSError as exc:
            raise SkillError("not_found" if isinstance(exc, FileNotFoundError) else "io",
                             f"{function.__name__}: {exc}") from exc
        except (UnicodeError, TypeError, ValueError, RecursionError) as exc:
            raise SkillError("validation", f"{function.__name__}: {exc}") from exc
    return call


class Manager(SourceMethods, ManagedMethods):
    resource_kind = 'skills'

    def __init__(self, home=None):
        self._store = Store(home)
        self.home = self._store.home
        self.store_id = self._store.id

    @operation
    def discover_collection(self, root) -> CollectionDiscovery:
        from .collections import discover
        return discover(root)

    @operation
    def import_collection(self, root, selections=None, *, all=False, expected_revision=None) -> CollectionImport:
        from .collections import import_selected
        return import_selected(self, root, selections, all=all, expected_revision=expected_revision)

    @operation
    def project(self, root) -> Project:
        return Project(self, str(fs.absolute(root)), str(self.home))

    def _project_service(self, root):
        from .projects import Projects
        return Projects(self, root)

    @operation
    def preview_project_connect(self, root, selections, *, skills_dir=None, adopt=False) -> ProjectPlan:
        return self._project_service(root).preview_connect(selections, skills_dir=skills_dir, adopt=adopt)

    @operation
    def preview_project_update(self, root, selections=None) -> ProjectPlan:
        return self._project_service(root).preview_update(selections)

    @operation
    def preview_project_disconnect(self, root, selections, *, forget=False) -> ProjectPlan:
        return self._project_service(root).preview_disconnect(selections, forget=forget)

    @operation
    def preview_project_restore(self, root, *, adopt=False) -> ProjectPlan:
        return self._project_service(root).preview_restore(adopt=adopt)

    @operation
    def apply_project_plan(self, root, plan: ProjectPlan) -> ProjectOperation:
        return self._project_service(root).apply(plan)

    @operation
    def project_connections(self, root) -> ProjectConnections:
        return self._project_service(root).connections()

    @operation
    def project_operations(self, root) -> list[ProjectOperation]:
        return self._project_service(root).operations()

    @operation
    def recover_project(self, root, identifier: str, *, action: str = "resume") -> ProjectOperation:
        return self._project_service(root).recover(identifier, action=action)

    @operation
    def save_project_variant(self, root, selection, variant_id, *, expected_revision, expected_digest) -> ManagedStatus:
        return self._project_service(root).save_variant(selection, variant_id,
            expected_revision=expected_revision, expected_digest=expected_digest)

    @operation
    def skill(self, publisher_id: str, skill_id: str) -> Skill:
        """Bind an explicit skill identity without reading or mutating the store."""
        return Skill(self, SkillReference(str(self.home), publisher_id, skill_id))

    @operation
    def open_skill(self, reference: SkillReference | dict[str, str]) -> Skill:
        if isinstance(reference, dict):
            reference = SkillReference.from_dict(reference)
        require(isinstance(reference, SkillReference), "Expected a SkillReference")
        return Skill(self, reference)

    @operation
    def register_skill(self, publisher: str, path, *, skill_id: str | None = None,
                       publisher_revision: int | None = None, version: str | None = None) -> RegistrationResult:
        """Capture usable accepted content and update diagnostics from one state."""
        from .managed import resolved_snapshot
        identifier = Path(path).name if skill_id is None else skill_id
        registered = self._managed().register(publisher, identifier, path,
            publisher_revision=publisher_revision, version=version)
        record = resolved_snapshot(self, registered["status"], registered["snapshot"])
        reference = SkillReference(str(self.home), publisher, identifier)
        return RegistrationResult(ResolvedSkill(self, reference, record))

    @operation
    def validate_managed(self, publisher: str, identifier: str, files: FileChanges | None = None,
                         *, revision: str | None = None) -> ValidationResult:
        return self._managed().validate(publisher, identifier,
            None if files is None else file_inputs(files), revision=revision)

    @operation
    def compare_managed(self, publisher: str, identifier: str, before_revision: str,
                        after_revision: str | None = None) -> ComparisonResult:
        return self._managed().compare(publisher, identifier, before_revision, after_revision)

    @operation
    def ensure_registered(self, publisher, path, *, skill_id=None, publisher_revision=None, version=None):
        return self._managed().ensure(publisher, Path(path).name if skill_id is None else skill_id,
                                      path, publisher_revision, version)

    def application(self, publisher, *, bundled_skills, publisher_revision=None, version=None):
        from .managed import Application
        return Application(self, publisher, bundled_skills, publisher_revision=publisher_revision, version=version)

    @operation
    def resolve_managed(self, publisher, identifier, *, revision=None):
        from .managed import resolve_managed
        return resolve_managed(self, publisher, identifier, revision=revision)

    @operation
    def read_managed_file(self, publisher, identifier, relative_path, *, revision=None):
        fs.relative(relative_path)
        resolved = self.resolve_managed(publisher, identifier, revision=revision)
        row = next((row for row in resolved["manifest"]["files"] if row["path"] == relative_path), None)
        require(row is not None, f"No regular file: {relative_path}", "not_found")
        return self._read_blob(row)

    @operation
    def export_managed(self, publisher, identifier, destination, *, revision=None):
        return self._managed().export(publisher, identifier, destination, revision=revision)

    @operation
    def release_managed(self, publisher, identifier, version, *, revision=None):
        return self._managed().release(publisher, identifier, version, revision=revision)

    def _publish_managed(self, resolved, publisher, identifier, version):
        """Publish a native release with the lifecycle's store lock already held."""
        require(isinstance(version, str) and VERSION.fullmatch(version), "Invalid immutable release label")
        source = "managed-" + digest([publisher, identifier])[7:31]
        name = resolved["name"]
        captured = {"schema": "skill-manager/managed-source-1", "id": source,
                    "publisher_id": publisher, "skill_id": identifier,
                    "managed_revision": resolved["resolution"]["revision"]}
        captured["revision"] = digest(captured)
        reference = f"{source}/{name}@{version}"
        target = self.home / "releases" / source / name / (version + ".json")
        if os.path.lexists(target):
            previous = self.resolve(reference)
            require(previous["digest"] == resolved["digest"], "Immutable release label already has different content", "conflict")
            return previous["release"]
        record = {"schema": "skill-manager/release-1", "reference": reference,
                  "source_id": source, "source_revision": captured["revision"],
                  "source_path": str(self.home / "managed" / publisher / (identifier + ".json")),
                  "name": name, "version": version, "digest": resolved["digest"],
                  "provenance": {"source": captured, "published_at": datetime.now(timezone.utc).isoformat()}}
        self._store.put_json(target, record, immutable=True)
        return record

    def _source_path(self, identifier):
        return self.home / "sources" / (validate_source_id(identifier) + ".json")

    def _source(self, identifier):
        row = json_read(self._source_path(identifier))
        require(isinstance(row, dict), "Source descriptor must be a mapping", "integrity")
        require(set(row) == {"schema", "id", "paths", "label", "owner", "writable", "revision"}
                and row["schema"] == "skill-manager/source-1" and row["id"] == identifier,
                "Invalid source descriptor", "integrity")
        require(isinstance(row["paths"], list) and row["paths"] and
                all(isinstance(p, str) and str(fs.absolute(p)) == p for p in row["paths"])
                and type(row["writable"]) is bool, "Invalid source paths or writable flag", "integrity")
        require(row["revision"] == digest({k: v for k, v in row.items() if k != "revision"}),
                "Source revision mismatch", "integrity")
        return row

    @operation
    def register_source(self, identifier, paths, *, label=None, writable=False, owner=None, expected_revision=None):
        validate_source_id(identifier)
        require(isinstance(paths, (list, tuple)) and paths, "paths must be a nonempty list of explicit roots")
        require(type(writable) is bool, "writable must be a bool")
        require(label is None or isinstance(label, str), "label must be a string or null")
        normalized = sorted({str(fs.absolute(p)) for p in paths})
        for path in normalized:
            # Reject symlinks in existing ancestors; absent sources are still registrable.
            current = Path("/")
            for part in Path(path).parts[1:]:
                current /= part
                try:
                    st = current.lstat()
                except FileNotFoundError:
                    break
                require(stat.S_ISDIR(st.st_mode), f"Source root/ancestor must be a directory without symlinks: {current}")
        row = {"schema": "skill-manager/source-1", "id": identifier, "paths": normalized,
               "label": label, "owner": owner, "writable": writable}
        row["revision"] = digest(row)
        with self._store.lock():
            try:
                previous = self._source(identifier)
            except FileNotFoundError:
                previous = None
            require((previous is None and expected_revision is None) or
                    (previous is not None and expected_revision == previous["revision"]),
                    "Source registration changed; supply its observed revision (null means create only)", "conflict")
            self._store.put_json(self._source_path(identifier), row)
        return self.source(identifier)

    @operation
    def source(self, identifier):
        row = self._source(identifier)
        locations = []
        for root in row["paths"]:
            error = None
            try:
                with fs.directory(root):
                    pass
            except (OSError, SkillError) as exc:
                error = exc.to_dict() if isinstance(exc, SkillError) else SkillError("io", str(exc)).to_dict()
            locations.append({"path": root, "available": error is None, "error": error})
        return {**row, "availability": locations}

    @operation
    def sources(self):
        rows = []
        try:
            with fs.directory(self.home / "sources") as fd:
                with os.scandir(fd) as it:
                    identifiers = sorted(item.name[:-5] for item in it if item.name.endswith(".json"))
        except FileNotFoundError:
            return rows
        for identifier in identifiers:
            try:
                rows.append(self.source(identifier))
            except SkillError as exc:
                rows.append({"schema": "skill-manager/source-1", "id": identifier, "error": exc.to_dict()})
        return rows

    @operation
    def unregister_source(self, identifier, *, expected_revision):
        with self._store.lock():
            previous = self._source(identifier)
            require(expected_revision == previous["revision"], "Source registration changed", "conflict")
            with fs.directory(self.home / "sources") as fd:
                (Path(fd) / (identifier + ".json")).unlink() if os.name == "nt" else os.unlink(identifier + ".json", dir_fd=fd)
            return {"schema": "skill-manager/source-removal-1", "source_id": identifier, "revision": expected_revision}

    def _discover(self, row):
        paths, errors = set(), []
        for root in row["paths"]:
            try:
                with fs.directory(root) as fd:
                    with os.scandir(fd) as it:
                        children = {item.name: item.stat(follow_symlinks=False).st_mode for item in it}
                    if "SKILL.md" in children:
                        paths.add(root)
                    else:
                        for name, mode in children.items():
                            if stat.S_ISLNK(mode):
                                errors.append(SkillError("validation", f"Symlink child excluded from discovery: {root}/{name}").to_dict())
                            if not stat.S_ISDIR(mode):
                                continue
                            try:
                                with fs.directory_at(fd, name) as child:
                                    with os.scandir(child) as it:
                                        if any(item.name == "SKILL.md" for item in it):
                                            paths.add(str(Path(root) / name))
                            except OSError as exc:
                                paths.add(str(Path(root) / name))
                                errors.append(SkillError("io", str(exc)).to_dict())
            except (OSError, SkillError) as exc:
                errors.append(exc.to_dict() if isinstance(exc, SkillError) else SkillError("io", str(exc)).to_dict())
        return sorted(paths), errors

    def _entry(self, row, path):
        result = {"schema": "skill-manager/entry-1", "id": digest([self.store_id, row["id"], path]),
                  "store": str(self.home), "store_id": self.store_id, "source_id": row["id"], "path": path,
                  "name": Path(path).name, "status": "invalid", "editable": row["writable"],
                  "digest": None, "digest_kind": None, "metadata": None, "manifest": None, "error": None}
        try:
            snapshot = fs.capture(path)
            result.update({k: snapshot[k] for k in ("digest", "digest_kind", "manifest", "error")})
            result["metadata"] = snapshot["manifest"]["metadata"]
            result["status"] = "invalid" if snapshot["error"] else "valid"
        except (OSError, SkillError) as exc:
            result["error"] = exc.to_dict() if isinstance(exc, SkillError) else SkillError("io", str(exc)).to_dict()
        return result

    @operation
    def catalog(self):
        sources, entries = self.sources(), []
        for row in sources:
            if "paths" not in row:
                continue
            paths, errors = self._discover(row)
            row["discovery_errors"] = errors
            entries.extend(self._entry(row, path) for path in paths)
        return {"schema": "skill-manager/catalog-1", "store": str(self.home),
                "store_id": self.store_id, "sources": sources, "entries": sorted(entries, key=lambda e: e["id"]),
                "managed": self.managed_catalog()}

    def _draft(self, identifier):
        require(isinstance(identifier, str) and DIGEST.fullmatch(identifier), "Draft selection requires an entry ID")
        for row in self.sources():
            if "paths" not in row:
                continue
            paths, _ = self._discover(row)
            for path in paths:
                if digest([self.store_id, row["id"], path]) == identifier:
                    return self._source(row["id"]), Path(path)
        raise SkillError("not_found", f"Unknown draft entry: {identifier}; inspect catalog for discovery errors")

    @operation
    def show(self, entry_id):
        if isinstance(entry_id, str) and "@" in entry_id:
            return self.resolve(entry_id)
        row, path = self._draft(entry_id)
        return self._entry(row, str(path))

    def _snapshot(self, selection):
        if isinstance(selection, str) and "@" in selection:
            resolved = self.resolve(selection)
            contents = {f["path"]: self._read_blob(f) for f in resolved["manifest"]["files"]}
            return {"manifest": resolved["manifest"], "digest": resolved["digest"], "digest_kind": "package",
                    "error": None, "contents": contents}
        _, path = self._draft(selection)
        return fs.capture(path)

    @operation
    def read_file(self, entry_id, relative_path):
        fs.relative(relative_path)
        snapshot = self._snapshot(entry_id)
        require(relative_path in snapshot["contents"], f"No regular file: {relative_path}", "not_found")
        return snapshot["contents"][relative_path]

    @operation
    def create_draft(self, source_id, name, files, *, path=None):
        skill_name(name)
        require(isinstance(files, dict) and "SKILL.md" in files, "files must include SKILL.md (may be a repairable draft)")
        directories = set()
        require(len(files) <= fs.MAX_ENTRIES, "Too many draft files")
        total = 0
        for rel, spec in files.items():
            parts = fs.relative(rel)
            require(isinstance(spec, dict) and set(spec) == {"content", "executable"}
                    and isinstance(spec["content"], bytes) and type(spec["executable"]) is bool,
                    "Each file requires bytes content and bool executable")
            require(len(spec["content"]) <= fs.MAX_FILE, f"File too large: {rel}")
            total += len(spec["content"])
            directories.update("/".join(parts[:i]) for i in range(1, len(parts)))
        require(total <= fs.MAX_TOTAL and len(directories) + len(files) <= fs.MAX_ENTRIES, "Draft exceeds package limits")
        fs.check_paths(sorted(directories), files)
        with self._store.lock():
            row = self._source(source_id)
            require(row["writable"], "Read-only source; register a writable source and explicitly fork", "unsupported")
            require(path is not None or len(row["paths"]) == 1, "Multiple roots: choose --path/a registered root")
            root = fs.absolute(path if path is not None else row["paths"][0])
            require(str(root) in row["paths"], "Draft root must be one of the registered collection roots")
            with fs.directory(root) as fd:
                require("SKILL.md" not in os.listdir(fd), "A direct skill registration is not a collection root")
            target = root / name
            require(not os.path.lexists(target), f"Draft already exists: {target}", "conflict")
            with self._stage(root) as stage:
                self._materialize(stage, directories, files)
                fs.capture(stage, name=name)
                fs.rename_absent(stage, target)
            return self._entry(row, str(target))

    @contextlib.contextmanager
    def _stage(self, parent):
        with fs.directory(parent) as fd:
            temporary = ".skill-stage-" + secrets.token_hex(16)
            (Path(fd) / temporary).mkdir(mode=0o700) if os.name == "nt" else os.mkdir(temporary, 0o700, dir_fd=fd)
            try:
                yield parent / temporary
            finally:
                try:
                    shutil.rmtree(Path(fd) / temporary) if os.name == "nt" else shutil.rmtree(temporary, dir_fd=fd)
                except FileNotFoundError:
                    pass

    def _materialize(self, stage, directories, files):
        os.chmod(stage, 0o755)
        for rel in sorted(directories):
            fs.make_directories(stage / rel)
            os.chmod(stage / rel, 0o755)
        for rel, spec in sorted(files.items()):
            fs.write_atomic(stage / rel, spec["content"], executable=spec["executable"])
            require(fs.executable(stage / rel) == spec["executable"],
                    f"Filesystem did not preserve executable permission: {rel}", "unsupported")

    @operation
    def save_file(self, entry_id, relative_path, content, *, executable=False, expected_digest):
        parts = fs.relative(relative_path)
        require(isinstance(content, bytes) and len(content) <= fs.MAX_FILE, "content must be bytes within the 64 MiB limit")
        require(type(executable) is bool, "executable must be a bool")
        with self._store.lock():
            row, path = self._draft(entry_id)
            require(row["writable"], "Read-only source; editing requires an explicit writable draft/fork", "unsupported")
            before = fs.capture(path)
            require(expected_digest == before["digest"], "Draft changed; inspect and retry with observed digest", "conflict")
            directory_set = set(before["manifest"]["directories"])
            directory_set.update("/".join(parts[:i]) for i in range(1, len(parts)))
            files = {f["path"] for f in before["manifest"]["files"]} | {relative_path}
            fs.check_paths(sorted(directory_set), files)
            require(len(directory_set) + len(files) <= fs.MAX_ENTRIES, "Draft exceeds entry limit")
            total = sum(len(v) for k, v in before["contents"].items() if k != relative_path) + len(content)
            require(total <= fs.MAX_TOTAL, "Draft exceeds total byte limit")
            fs.make_directories(path.joinpath(*parts[:-1]))
            # Recheck after preparation, before the single-file commit.
            current = fs.capture(path)
            # New empty parent directories can change the digest; compare existing bytes/permissions directly.
            require(current["manifest"]["files"] == before["manifest"]["files"], "Draft changed while preparing edit", "conflict")
            require(set(current["manifest"]["directories"]) == directory_set,
                    "Draft directories changed while preparing edit", "conflict")
            fs.write_atomic(path / relative_path, content, executable=executable)
            return self._entry(row, str(path))

    @operation
    def validate(self, entry_id):
        try:
            inspection = self.show(entry_id)
        except SkillError as exc:
            if exc.code not in ("integrity", "io"):
                raise
            return {"schema": "skill-manager/validation-1", "entry_id": entry_id,
                    "valid": False, "diagnostics": [exc.to_dict()], "digest": None,
                    "digest_kind": None, "manifest": None}
        error = inspection.get("error")
        return {"schema": "skill-manager/validation-1", "entry_id": entry_id,
                "valid": error is None, "diagnostics": [error] if error else [],
                "digest": inspection["digest"], "digest_kind": inspection.get("digest_kind", "package"),
                "manifest": inspection["manifest"]}

    @operation
    def release(self, entry_id, version, *, expected_digest):
        require(isinstance(version, str) and VERSION.fullmatch(version), "Invalid immutable release label")
        with self._store.lock():
            source, path = self._draft(entry_id)
            snapshot = fs.capture(path)
            require(snapshot["error"] is None, f"Release requires a valid skill: {snapshot['error']}")
            require(expected_digest == snapshot["digest"], "Draft changed before publication", "conflict")
            name = snapshot["manifest"]["name"]
            target = self.home / "releases" / source["id"] / name / (version + ".json")
            if os.path.lexists(target):
                previous = self.resolve(f"{source['id']}/{name}@{version}")
                require(previous["digest"] == snapshot["digest"], "Immutable release label already has different content", "conflict")
                return previous["release"]
            record = {"schema": "skill-manager/release-1", "reference": f"{source['id']}/{name}@{version}",
                      "source_id": source["id"], "source_revision": source["revision"], "source_path": str(path),
                      "name": name, "version": version, "digest": snapshot["digest"],
                      "provenance": {"source": {k: v for k, v in source.items()},
                                     "published_at": datetime.now(timezone.utc).isoformat()}}
            self._store.capture(snapshot)
            # Noncooperating draft writers must not silently publish stale content.
            require(fs.capture(path)["digest"] == snapshot["digest"], "Draft changed during publication", "conflict")
            self._store.put_json(target, record, immutable=True)
            return record

    def _release_record(self, source, name, version):
        root = self.home / "releases" / source / name
        if DIGEST.fullmatch(version):
            with fs.directory(root) as fd:
                labels = sorted(f[:-5] for f in os.listdir(fd) if f.endswith(".json") and VERSION.fullmatch(f[:-5]))
            for label in labels:
                record = self._release_record(source, name, label)
                if record["digest"] == version:
                    return record
            raise SkillError("not_found", "Digest has no publication in the selected source/name namespace")
        record = json_read(root / (version + ".json"))
        require(isinstance(record, dict) and set(record) == {"schema", "reference", "source_id", "source_revision",
                "source_path", "name", "version", "digest", "provenance"}, "Invalid release record", "integrity")
        require(record["schema"] == "skill-manager/release-1" and record["source_id"] == source and
                record["name"] == name and record["version"] == version and
                record["reference"] == f"{source}/{name}@{version}" and
                isinstance(record["digest"], str) and DIGEST.fullmatch(record["digest"]),
                "Release identity mismatch", "integrity")
        require(isinstance(record["source_revision"], str) and DIGEST.fullmatch(record["source_revision"])
                and isinstance(record["source_path"], str) and str(fs.absolute(record["source_path"])) == record["source_path"]
                and isinstance(record["provenance"], dict) and set(record["provenance"]) == {"source", "published_at"},
                "Invalid release provenance", "integrity")
        captured = record["provenance"]["source"]
        require(isinstance(captured, dict) and captured.get("id") == source
                and captured.get("revision") == record["source_revision"]
                and digest({k: v for k, v in captured.items() if k != "revision"}) == record["source_revision"],
                "Captured source provenance mismatch", "integrity")
        return record

    def _read_blob(self, row):
        try:
            content = fs.read_path(self._store.blob_path(row["digest"]))
        except (OSError, SkillError) as exc:
            raise SkillError("integrity", f"Missing or unsafe blob for {row['path']}: {exc}") from exc
        require(len(content) == row["size"] and byte_digest(content) == row["digest"],
                f"Blob integrity failure: {row['path']}", "integrity")
        return content

    def _manifest(self, identity, name):
        try:
            manifest = json_read(self._store.object_path(identity))
        except OSError as exc:
            raise SkillError("integrity", f"Missing or unsafe package object: {exc}") from exc
        require(isinstance(manifest, dict) and set(manifest) == {"schema", "name", "metadata", "directories", "files"}
                and manifest["schema"] == "skill-manager/package-1" and manifest["name"] == name,
                "Invalid package manifest", "integrity")
        require(digest(manifest) == identity, "Manifest digest mismatch", "integrity")
        directories, files = manifest["directories"], manifest["files"]
        require(isinstance(directories, list) and all(isinstance(d, str) for d in directories)
                and directories == sorted(directories) and isinstance(files, list)
                and len(directories) + len(files) <= fs.MAX_ENTRIES, "Invalid manifest paths/entry limit", "integrity")
        paths, contents, total = [], {}, 0
        for f in files:
            require(isinstance(f, dict) and set(f) == {"path", "size", "digest", "executable"}
                    and isinstance(f["path"], str) and type(f["size"]) is int and 0 <= f["size"] <= fs.MAX_FILE
                    and isinstance(f["digest"], str) and DIGEST.fullmatch(f["digest"])
                    and type(f["executable"]) is bool, "Invalid file manifest row", "integrity")
            paths.append(f["path"])
            content = self._read_blob(f)
            total += len(content)
            contents[f["path"]] = content
        require(paths == sorted(paths) and total <= fs.MAX_TOTAL, "Invalid file order/package limit", "integrity")
        try:
            fs.check_paths(directories, paths)
            for path in [*directories, *paths]:
                parts = path.split("/")
                require(all("/".join(parts[:i]) in directories for i in range(1, len(parts))),
                        f"Missing parent directory in manifest: {path}")
            require("SKILL.md" in contents, "Missing SKILL.md blob")
            require(parse(contents["SKILL.md"], name) == manifest["metadata"], "Metadata does not match SKILL.md")
        except SkillError as exc:
            raise SkillError("integrity", exc.message) from exc
        return manifest

    @operation
    def resolve(self, reference):
        source, name, version = parse_reference(reference)
        record = self._release_record(source, name, version)
        manifest = self._manifest(record["digest"], name)
        return {"schema": "skill-manager/resolution-1", "reference": reference,
                "store": str(self.home), "store_id": self.store_id, "source_id": source, "name": name,
                "digest": record["digest"], "digest_kind": "package", "release": record, "manifest": manifest,
                "object": str(self._store.object_path(record["digest"])),
                "blobs": {f["path"]: str(self._store.blob_path(f["digest"])) for f in manifest["files"]}}

    @operation
    def releases(self, source_id=None, name=None):
        """List retained versions, including publications from removed sources."""
        if source_id is not None:
            validate_source_id(source_id)
        if name is not None:
            skill_name(name)
        root = self.home / "releases"
        try:
            with fs.directory(root) as fd:
                sources = sorted(s for s in os.listdir(fd) if SOURCE.fullmatch(s))
        except FileNotFoundError:
            return []
        results = []
        for source in sources:
            if source_id is not None and source != source_id:
                continue
            try:
                with fs.directory(root / source) as fd:
                    names = sorted(n for n in os.listdir(fd) if len(n) <= 64 and NAME.fullmatch(n))
                for skill in names:
                    if name is not None and skill != name:
                        continue
                    with fs.directory(root / source / skill) as fd:
                        versions = sorted(p[:-5] for p in os.listdir(fd) if p.endswith(".json") and VERSION.fullmatch(p[:-5]))
                    for version in versions:
                        ref = f"{source}/{skill}@{version}"
                        try:
                            results.append(self.resolve(ref)["release"])
                        except SkillError as exc:
                            results.append({"schema": "skill-manager/release-entry-1", "reference": ref,
                                            "source_id": source, "name": skill, "version": version, "error": exc.to_dict()})
            except OSError as exc:
                results.append({"schema": "skill-manager/release-entry-1", "source_id": source,
                                "error": SkillError("io", str(exc)).to_dict()})
        return results

    @operation
    def diff(self, before, after):
        left, right = self._snapshot(before), self._snapshot(after)
        a, b = ({f["path"]: f for f in s["manifest"]["files"]} for s in (left, right))
        changed = [{"path": p, "before": a[p], "after": b[p],
                    "bytes_changed": a[p]["digest"] != b[p]["digest"],
                    "executable_changed": a[p]["executable"] != b[p]["executable"]}
                   for p in sorted(a.keys() & b.keys()) if a[p] != b[p]]
        ad, bd = (set(s["manifest"]["directories"]) for s in (left, right))
        am, bm = left["manifest"]["metadata"], right["manifest"]["metadata"]
        return {"schema": "skill-manager/diff-1", "before": before, "after": after,
                "before_digest": left["digest"], "after_digest": right["digest"],
                "added": [b[p] for p in sorted(b.keys() - a.keys())],
                "removed": [a[p] for p in sorted(a.keys() - b.keys())], "changed": changed,
                "directories_added": sorted(bd - ad), "directories_removed": sorted(ad - bd),
                "metadata_changed": am != bm, "metadata_before": am, "metadata_after": bm}

    @operation
    def export(self, reference, destination):
        return self._export_resolved(self.resolve(reference), destination)

    def _export_resolved(self, resolved, destination, *, locked=False):
        target = fs.absolute(destination)
        require(target.name == resolved["name"], "Destination basename must equal the skill name")
        # Destination locks serialize package operations; Linux no-replace commit also protects other writers.
        with contextlib.nullcontext() if locked else self._store.lock():
            if os.path.lexists(target):
                return self._identical(resolved, target)
            require(target.parent.is_dir(), "Export parent must already exist; choose an explicit caller-owned directory")
            with self._stage(target.parent) as stage:
                manifest = resolved["manifest"]
                files = {}
                for row in manifest["files"]:
                    content = self._read_blob(row)
                    files[row["path"]] = {"content": content, "executable": row["executable"]}
                self._materialize(stage, manifest["directories"], files)
                require(fs.capture(stage, name=resolved["name"])["digest"] == resolved["digest"],
                        "Staged export failed package integrity verification", "integrity")
                try:
                    fs.rename_absent(stage, target)
                except SkillError as exc:
                    if exc.code != "conflict":
                        raise
                    return self._identical(resolved, target)
            return self._export_result(resolved, target, "exported")

    def _identical(self, resolved, target):
        try:
            snapshot = fs.capture(target)
            require(snapshot["error"] is None and snapshot["digest"] == resolved["digest"],
                    "Destination exists with different content; choose an absent package directory", "conflict")
        except (OSError, SkillError) as exc:
            raise SkillError("conflict", f"Existing destination is not an identical safe package: {target}: {exc}") from exc
        return self._export_result(resolved, target, "identical")

    def _export_result(self, resolved, target, status):
        return {"schema": "skill-manager/export-1", "reference": resolved["reference"],
                "digest": resolved["digest"], "destination": str(target), "status": status}
