"""Read-only filesystem fingerprints for explicitly selected record trees."""

import os
import stat
from pathlib import Path


def scan(home, sections):
    errors = []
    found = {}
    for top in [home, *(home / section for section in sections)]:
        try:
            info = top.lstat()
            found[top] = (info.st_mode, info.st_mtime_ns, info.st_size, info.st_ino)
            if not stat.S_ISDIR(info.st_mode):
                errors.append(OSError(f"Expected a store directory: {top}"))
                continue
        except FileNotFoundError:
            continue
        except OSError as error:
            errors.append(error)
            continue
        if top == home:
            continue
        for directory, directories, files in os.walk(top, onerror=errors.append):
            for name in sorted([*directories, *files]):
                path = Path(directory) / name
                try:
                    info = path.lstat()
                    if stat.S_ISLNK(info.st_mode):
                        errors.append(OSError(f"Store inspection does not follow symlinks: {path}"))
                        if name in directories:
                            directories.remove(name)
                        continue
                    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                        errors.append(OSError(f"Unsupported store file type: {path}"))
                        continue
                    found[path] = (info.st_mode, info.st_mtime_ns, info.st_size, info.st_ino)
                except OSError as error:
                    errors.append(error)
    return found, errors
