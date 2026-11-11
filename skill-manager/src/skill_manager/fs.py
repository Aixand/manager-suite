"""Descriptor-relative reads and no-replace directory commits on Linux/macOS."""
import contextlib
import ctypes
import errno
import os
from pathlib import Path
import stat
import secrets
import sys
import unicodedata

from .codec import byte_digest, digest
from .errors import SkillError, require
from .metadata import parse

MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_ENTRIES = 4096
MAX_DEPTH = 32
RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
            *(f"lpt{i}" for i in range(1, 10))}


def relative(value):
    require(isinstance(value, str) and value and len(value.encode("utf-8")) <= 4096,
            "Package path must be a nonempty portable relative path")
    parts = value.split("/")
    require(len(parts) <= MAX_DEPTH, f"Package path exceeds {MAX_DEPTH} components")
    for part in parts:
        require(part and part not in (".", "..") and not any(ord(c) < 32 or ord(c) == 127 for c in part)
                and not any(c in part for c in '\\:*?"<>|') and not part.endswith((" ", "."))
                and part.split(".")[0].casefold() not in RESERVED and len(part.encode("utf-8")) <= 255,
                f"Nonportable/escaping package path: {value}")
    return parts


def path_key(value):
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", value).casefold())


def check_paths(directories, files):
    seen = {}
    for path in [*directories, *files]:
        relative(path)
        key = path_key(path)
        require(key not in seen, f"Portable path collision: {path} and {seen.get(key)}")
        seen[key] = path
    file_set = set(files)
    for path in [*directories, *files]:
        parts = path.split("/")
        for i in range(1, len(parts)):
            require("/".join(parts[:i]) not in file_set, f"File/directory collision: {path}")


def absolute(path):
    try:
        supplied = os.fspath(path)
        require(isinstance(supplied, str) and supplied, "Filesystem paths must be nonempty strings")
        value = os.path.abspath(os.path.expanduser(supplied))
        value.encode("utf-8")
        require("\0" not in value, "Path contains NUL")
        return Path(value)
    except (TypeError, UnicodeError, ValueError) as exc:
        raise SkillError("validation", f"Invalid filesystem path: {exc}") from exc


@contextlib.contextmanager
def directory(path):
    """Do not follow symlinks in any ancestor, including during races."""
    require(hasattr(os, "O_NOFOLLOW"), "No-follow filesystem access requires POSIX", "unsupported")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in absolute(path).parts[1:]:
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
        yield fd
    finally:
        os.close(fd)


@contextlib.contextmanager
def directory_at(parent, name):
    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        yield child
    finally:
        os.close(child)


def stat_at(parent, name):
    return os.stat(name, dir_fd=parent, follow_symlinks=False)


def directory_stat(parent):
    return parent.stat() if isinstance(parent, Path) else os.fstat(parent)


def executable(path):
    return bool(Path(path).stat().st_mode & 0o111)


def _stamp(st):
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_mode


def read_at(fd, name, *, limit=None):
    limit = MAX_FILE if limit is None else limit
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        before = os.fstat(handle)
        require(stat.S_ISREG(before.st_mode), f"Special file/symlink is forbidden: {name}")
        require(before.st_size <= limit, f"File exceeds {limit} bytes: {name}")
        with os.fdopen(handle, "rb", closefd=False) as stream:
            content = stream.read(limit + 1)
        require(len(content) <= limit, f"File exceeds {limit} bytes: {name}")
        require(_stamp(before) == _stamp(os.fstat(handle)), f"File changed during inspection: {name}", "conflict")
        return content, bool(before.st_mode & 0o111)
    finally:
        os.close(handle)


def read_path(path, *, limit=None):
    path = absolute(path)
    with directory(path.parent) as fd:
        return read_at(fd, path.name, limit=limit)[0]


def capture(path, *, name=None):
    """Capture bytes once; invalid frontmatter still supplies a repair digest."""
    path = absolute(path)
    files, directories, contents = [], [], {}
    count, total = 0, 0

    def walk(fd, prefix=""):
        nonlocal count, total
        before = directory_stat(fd)
        with os.scandir(fd) as iterator:
            names = sorted(item.name for item in iterator)
        for item in names:
            rel = prefix + item
            relative(rel)
            count += 1
            require(count <= MAX_ENTRIES, f"Package exceeds {MAX_ENTRIES} entries")
            st = stat_at(fd, item)
            if stat.S_ISDIR(st.st_mode):
                directories.append(rel)
                with directory_at(fd, item) as child:
                    walk(child, rel + "/")
            else:
                require(stat.S_ISREG(st.st_mode), f"Symlink/special file is forbidden: {rel}")
                data, executable = read_at(fd, item)
                total += len(data)
                require(total <= MAX_TOTAL, f"Package exceeds {MAX_TOTAL} total bytes")
                contents[rel] = data
                files.append({"path": rel, "size": len(data), "digest": byte_digest(data), "executable": executable})
        require(_stamp(before) == _stamp(directory_stat(fd)), "Directory changed during inspection", "conflict")

    with directory(path) as fd:
        walk(fd)
    directories.sort()
    files.sort(key=lambda f: f["path"])
    check_paths(directories, contents)
    repair = {"schema": "skill-manager/draft-files-1", "directories": directories, "files": files}
    metadata, error = None, None
    try:
        require("SKILL.md" in contents, "Missing regular SKILL.md")
        metadata = parse(contents["SKILL.md"], name or path.name)
    except SkillError as exc:
        error = exc.to_dict()
    manifest = {"schema": "skill-manager/package-1", "name": name or path.name,
                "metadata": metadata, "directories": directories, "files": files}
    return {"manifest": manifest, "digest": digest(repair if error else manifest),
            "digest_kind": "draft-files" if error else "package", "error": error, "contents": contents}


def rename_absent(source, destination):
    """Atomic no-replace publication retains even a competing empty directory."""
    source, destination = absolute(source), absolute(destination)
    libc = ctypes.CDLL(None, use_errno=True)
    api = "renameatx_np" if sys.platform == "darwin" else "renameat2"
    # Darwin's RENAME_EXCL is 0x4 in sys/stdio.h; Linux's RENAME_NOREPLACE is 0x1.
    flag = 4 if sys.platform == "darwin" else 1
    function = getattr(libc, api, None)
    require(function is not None, f"Atomic directory publication requires {api}", "unsupported")
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    with directory(source.parent) as src, directory(destination.parent) as dst:
        if function(src, os.fsencode(source.name), dst, os.fsencode(destination.name), flag):
            error = ctypes.get_errno()
            if error in (errno.EEXIST, errno.ENOTEMPTY):
                raise SkillError("conflict", f"Destination already exists: {destination}")
            if error in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
                raise SkillError("unsupported", "Filesystem lacks atomic no-replace directory publication")
            raise OSError(error, os.strerror(error), str(destination))


def write_atomic(path, content, *, executable=False, immutable=False):
    """Atomic one-file replacement, or immutable link publication."""
    path = absolute(path)
    with directory(path.parent) as fd:
        temporary = ".skill-tmp-" + secrets.token_hex(16)
        handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fchmod(stream.fileno(), 0o755 if executable else 0o644)
                os.fsync(stream.fileno())
            if immutable:
                try:
                    os.link(temporary, path.name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                except FileExistsError:
                    require(read_at(fd, path.name)[0] == content, f"Immutable content mismatch: {path}", "integrity")
            else:
                os.replace(temporary, path.name, src_dir_fd=fd, dst_dir_fd=fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=fd)
            except OSError:
                pass


def make_directories(path):
    """Create ancestors without traversing a symlink."""
    path = absolute(path)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            try:
                os.mkdir(part, 0o755, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
    finally:
        os.close(fd)


if os.name == "nt":
    from .windows_fs import directory, directory_at, read_at, stat_at, rename_absent, write_atomic, make_directories, executable
