"""Windows filesystem adapter; pinned parents retain no-link traversal guarantees."""
import contextlib
import os
from pathlib import Path
import secrets
from aixand_filesystem import PinnedPath
from . import fs
from .errors import SkillError, require


def _native(path):
    value = str(fs.absolute(path))
    if value.startswith('\\\\?\\'):
        return value
    return '\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value


@contextlib.contextmanager
def directory(path):
    with PinnedPath(fs.absolute(path), [], directory=True) as pinned:
        yield pinned.path


@contextlib.contextmanager
def directory_at(parent, name):
    with directory(Path(parent) / name) as path:
        yield path


def executable(path):
    try:
        return Path(_native(path) + ':aixand.exec').read_text() == '1'
    except FileNotFoundError:
        return Path(path).suffix.lower() in ('.exe', '.com', '.cmd', '.bat')


def read_at(parent, name, *, limit=None):
    limit = fs.MAX_FILE if limit is None else limit
    with PinnedPath(parent, [name]) as pinned:
        before = os.fstat(pinned.file.fileno())
        require(before.st_size <= limit, f'File exceeds {limit} bytes: {name}')
        content = pinned.file.read(limit + 1)
        require(len(content) <= limit, f'File exceeds {limit} bytes: {name}')
        require(fs._stamp(before) == fs._stamp(os.fstat(pinned.file.fileno())),
                f'File changed during inspection: {name}', 'conflict')
        return content, executable(pinned.path)


def stat_at(parent, name):
    info = (Path(parent) / name).lstat()
    require(not getattr(info, 'st_file_attributes', 0) & 0x400,
            f'Symlink/junction is forbidden: {name}')
    return info


def rename_absent(source, destination):
    source, destination = fs.absolute(source), fs.absolute(destination)
    with directory(source.parent), directory(destination.parent):
        # MoveFile on Windows fails atomically if the destination already exists.
        try:
            os.rename(source, destination)
        except FileExistsError:
            raise SkillError('conflict', f'Destination already exists: {destination}') from None


def write_atomic(path, content, *, executable=False, immutable=False):
    path = fs.absolute(path)
    with directory(path.parent):
        temporary = path.with_name('.skill-tmp-' + secrets.token_hex(16))
        try:
            with open(_native(temporary), 'xb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            # NTFS alternate data retains the portable executable bit without
            # inventing POSIX permissions for ordinary Windows files.
            Path(_native(temporary) + ':aixand.exec').write_text('1' if executable else '0')
            if immutable:
                try:
                    os.link(_native(temporary), _native(path))
                except FileExistsError:
                    require(read_at(path.parent, path.name)[0] == content,
                            f'Immutable content mismatch: {path}', 'integrity')
            else:
                os.replace(_native(temporary), _native(path))
        finally:
            Path(_native(temporary)).unlink(missing_ok=True)


def make_directories(path):
    path = fs.absolute(path)
    with contextlib.ExitStack() as guards:
        current = Path(path.anchor)
        guards.enter_context(directory(current))
        for part in path.parts[1:]:
            current /= part
            current.mkdir(exist_ok=True)
            guards.enter_context(directory(current))
