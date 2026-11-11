"""Validated, bounded store archives and staged restoration without merging."""

import errno
import hashlib
import os
import stat
import tempfile
import zipfile
import zlib
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from ..codec import MAX_BYTES, canonical, parse
from ..errors import PromptError, require
from ..managed.state import engine
from ..prompts.definitions import DIGEST, NAME, VERSION
from ..sources.registry import Registry
from ..storage.files import ensure_directory, sync_directory
from ..storage.inventory import scan
from .integrity import SECTIONS, check_store
from .releases import Store

MAX_FILES = 100_000
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
SCHEMA = "prompt-manager/store-backup-1"


def _sha(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _file_digest(path):
    require(stat.S_ISREG(path.stat().st_mode), "Archive must be a regular file", "integrity")
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return "sha256:" + result.hexdigest()


def _record_path(value):
    require(
        isinstance(value, str) and "\\" not in value and "\0" not in value,
        "Invalid backup record path",
        "integrity",
    )
    path = PurePosixPath(value)
    parts = path.parts
    require(
        not path.is_absolute()
        and str(path) == value
        and ".." not in parts
        and parts
        and parts[0] in SECTIONS
        and len(parts) == SECTIONS[parts[0]]
        and path.suffix == ".json",
        "Invalid backup record layout",
        "integrity",
    )
    section, stem = parts[0], path.stem
    if section == "objects":
        valid = DIGEST.fullmatch("sha256:" + stem)
    elif section == "managed-history":
        valid = all(NAME.fullmatch(part) for part in parts[1:-1]) and DIGEST.fullmatch(
            "sha256:" + stem
        )
    elif section == "releases":
        valid = NAME.fullmatch(parts[1]) and VERSION.fullmatch(stem)
    else:
        valid = all(NAME.fullmatch(part) for part in (*parts[1:-1], stem))
    require(bool(valid), "Invalid backup record identity", "integrity")
    return path


def _write(root, relative, data, *, durable):
    path = root.joinpath(*_record_path(relative).parts)
    if durable:
        ensure_directory(path.parent)
    else:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(
        path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        if durable:
            stream.flush()
            os.fsync(stream.fileno())
    if durable:
        sync_directory(path.parent)


def _validate(root):
    store = Store(root)
    root = store.home
    report = check_store(store, Registry(root), engine(store), include_external=False)
    errors = [item for item in report["findings"] if item["severity"] == "error"]
    detail = errors[0]["message"] if errors else "incomplete or changing reference graph"
    require(
        report["ok"] and report["references_complete"],
        "Backup store validation failed: " + detail,
        "integrity",
    )
    warnings = [
        {**item, "path": str(Path(item["path"]).relative_to(root))} for item in report["findings"]
    ]
    sources = [{"id": row["id"], "paths": row["paths"]} for row in Registry(root).list("sources")]
    return warnings, sources


def _capture(home, stage):
    before, errors = scan(home, SECTIONS)
    require(not errors, "Cannot safely enumerate source store: " + str(errors[:1]), "integrity")
    files, excluded, total = {}, [], 0
    if home.exists():
        excluded.extend(
            path.name + ("/" if stat.S_ISDIR(path.lstat().st_mode) else "")
            for path in home.iterdir()
            if path.name not in SECTIONS
        )
    for path, info in sorted(before.items()):
        if path == home or stat.S_ISDIR(info[0]):
            continue
        relative = path.relative_to(home).as_posix()
        if path.name.startswith((".registry-", ".publish-")) or path.suffix != ".json":
            excluded.append(relative)
            continue
        _record_path(relative)
        require(len(files) < MAX_FILES, "Backup exceeds record count limit", "size")
        with path.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
        total += len(data)
        require(
            len(data) <= MAX_BYTES and total <= MAX_TOTAL_BYTES,
            "Backup exceeds record or total size limit",
            "size",
        )
        _write(stage, relative, data, durable=False)
        files[relative] = {"size": len(data), "digest": _sha(data)}
    after, errors = scan(home, SECTIONS)
    require(
        not errors and before == after,
        "Source store changed during backup; retry with writers idle",
        "conflict",
    )
    warnings, sources = _validate(stage)
    manifest = {
        "schema": SCHEMA,
        "source_store": str(home),
        "files": files,
        "external_drafts_included": False,
        "external_sources": sources,
        "excluded": sorted(excluded),
    }
    require(len(canonical(manifest)) <= MAX_BYTES, "Backup manifest exceeds size limit", "size")
    return manifest, warnings


def _zip_write(archive, name, data):
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    archive.writestr(info, data)


def backup_store(home, destination):
    home, destination = Path(home).resolve(), Path(destination).resolve()
    require(
        not destination.is_relative_to(home),
        "Backup archive must be outside the source store",
        "conflict",
    )
    with tempfile.TemporaryDirectory(prefix="prompt-backup-") as directory:
        stage = Path(directory)
        manifest, warnings = _capture(home, stage)
        ensure_directory(destination.parent)
        descriptor, temporary = tempfile.mkstemp(prefix=".backup-", dir=destination.parent)
        temporary = Path(temporary)
        try:
            with os.fdopen(descriptor, "w+b") as stream:
                with zipfile.ZipFile(stream, "w") as archive:
                    _zip_write(archive, "manifest.json", canonical(manifest))
                    for relative in manifest["files"]:
                        _zip_write(archive, relative, (stage / relative).read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
            with _archive(temporary) as verification:
                require(
                    _manifest(verification) == manifest,
                    "Written backup manifest mismatch",
                    "integrity",
                )
                for _ in _verified_records(verification, manifest):
                    pass
            identity = _file_digest(temporary)
            try:
                os.link(temporary, destination)
            except FileExistsError:
                require(
                    _file_digest(destination) == identity,
                    "Backup destination already contains different content",
                    "conflict",
                )
            sync_directory(destination.parent)
        finally:
            temporary.unlink(missing_ok=True)
    return {
        "schema": "prompt-manager/store-backup-result-1",
        "path": str(destination),
        "source_store": str(home),
        "digest": identity,
        "validated": True,
        "file_count": len(manifest["files"]),
        "content_bytes": sum(row["size"] for row in manifest["files"].values()),
        "external_drafts_included": False,
        "external_sources": manifest["external_sources"],
        "excluded": manifest["excluded"],
        "warnings": warnings,
    }


def _manifest(archive):
    entries = archive.infolist()
    require(len(entries) <= MAX_FILES + 1, "Backup exceeds record count limit", "size")
    names = [entry.filename for entry in entries]
    require(
        len(set(names)) == len(names) and "manifest.json" in names,
        "Backup has duplicate entries or no manifest",
        "integrity",
    )
    for entry in entries:
        mode = entry.external_attr >> 16
        require(
            entry.filename == entry.orig_filename
            and "\0" not in entry.filename
            and not entry.is_dir()
            and stat.S_IFMT(mode) in {0, stat.S_IFREG}
            and not entry.flag_bits & 1
            and entry.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED},
            "Backup contains an unsupported entry type",
            "integrity",
        )
        require(0 <= entry.file_size <= MAX_BYTES, "Backup entry exceeds size limit", "size")
    require(
        sum(entry.file_size for entry in entries) <= MAX_TOTAL_BYTES + MAX_BYTES,
        "Backup exceeds total size limit",
        "size",
    )
    with archive.open("manifest.json") as stream:
        manifest = parse(stream.read(MAX_BYTES + 1), "backup manifest")
    fields = {
        "schema",
        "source_store",
        "files",
        "external_drafts_included",
        "external_sources",
        "excluded",
    }
    require(
        isinstance(manifest, dict) and set(manifest) == fields and manifest["schema"] == SCHEMA,
        "Unsupported backup manifest",
        "integrity",
    )
    require(
        isinstance(manifest["source_store"], str)
        and manifest["source_store"]
        and "\0" not in manifest["source_store"]
        and manifest["external_drafts_included"] is False
        and isinstance(manifest["files"], dict)
        and isinstance(manifest["external_sources"], list)
        and isinstance(manifest["excluded"], list)
        and all(isinstance(item, str) for item in manifest["excluded"]),
        "Invalid backup metadata",
        "integrity",
    )
    require(
        set(names) == {"manifest.json", *manifest["files"]},
        "Backup entries do not match manifest",
        "integrity",
    )
    total = 0
    for relative, row in manifest["files"].items():
        _record_path(relative)
        require(
            isinstance(row, dict)
            and set(row) == {"size", "digest"}
            and type(row["size"]) is int
            and 0 <= row["size"] <= MAX_BYTES
            and isinstance(row["digest"], str)
            and DIGEST.fullmatch(row["digest"]),
            "Invalid backup record metadata",
            "integrity",
        )
        require(
            archive.getinfo(relative).file_size == row["size"],
            "Backup entry size mismatch",
            "integrity",
        )
        total += row["size"]
    require(total <= MAX_TOTAL_BYTES, "Backup exceeds total size limit", "size")
    return manifest


@contextmanager
def _archive(source):
    info = source.stat()
    require(stat.S_ISREG(info.st_mode), "Backup archive must be a regular file", "integrity")
    require(
        info.st_size <= MAX_TOTAL_BYTES + 2 * MAX_BYTES, "Backup archive exceeds size limit", "size"
    )
    try:
        with zipfile.ZipFile(source) as archive:
            yield archive
    except (zipfile.BadZipFile, EOFError, zlib.error) as error:
        raise PromptError("integrity", "Invalid backup archive: " + str(error)) from error


def _verified_records(archive, manifest):
    for relative, row in manifest["files"].items():
        with archive.open(relative) as stream:
            data = stream.read(MAX_BYTES + 1)
        require(
            len(data) == row["size"] and _sha(data) == row["digest"],
            "Backup record checksum mismatch: " + relative,
            "integrity",
        )
        yield relative, data


def _extract(source, stage):
    with _archive(source) as archive:
        manifest = _manifest(archive)
        for relative, data in _verified_records(archive, manifest):
            _write(stage, relative, data, durable=True)
    warnings, sources = _validate(stage)
    require(
        sources == manifest["external_sources"],
        "Backup external-source metadata mismatch",
        "integrity",
    )
    return manifest, warnings


def restore_store(home, source):
    home, source = Path(home).resolve(), Path(source).resolve()
    require(not home.exists(), "Restore requires a new store path that does not exist", "conflict")
    before = source.stat()
    require(stat.S_ISREG(before.st_mode), "Backup archive must be a regular file", "integrity")
    ensure_directory(home.parent)
    with tempfile.TemporaryDirectory(prefix=".prompt-restore-", dir=home.parent) as directory:
        stage = Path(directory) / "store"
        stage.mkdir(mode=0o700)
        manifest, warnings = _extract(source, stage)
        identity = _file_digest(source)
        after = source.stat()
        require(
            (before.st_mtime_ns, before.st_size, before.st_ino)
            == (after.st_mtime_ns, after.st_size, after.st_ino),
            "Backup archive changed during restore",
            "conflict",
        )
        require(not home.exists(), "Restore destination appeared during validation", "conflict")
        sync_directory(stage)
        try:
            os.rename(stage, home)
        except OSError as error:
            if error.errno in {errno.EEXIST, errno.ENOTEMPTY}:
                raise PromptError("conflict", "Restore destination is no longer empty") from error
            raise
        sync_directory(home.parent)
    return {
        "schema": "prompt-manager/store-restore-result-1",
        "store": str(home),
        "path": str(source),
        "source_store": manifest["source_store"],
        "digest": identity,
        "validated": True,
        "file_count": len(manifest["files"]),
        "content_bytes": sum(row["size"] for row in manifest["files"].values()),
        "external_drafts_included": False,
        "external_sources": manifest["external_sources"],
        "excluded": manifest["excluded"],
        "warnings": warnings,
    }
