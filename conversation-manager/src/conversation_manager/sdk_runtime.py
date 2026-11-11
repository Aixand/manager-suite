"""Load an explicitly installed, versioned provider dependency directory."""
import importlib
from pathlib import Path
import sys


def activate(directory):
    if not directory:
        return
    path = Path(directory)
    if not path.is_absolute() or not path.is_dir() or any(p.is_symlink() for p in (path, *path.parents)):
        return
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
        importlib.invalidate_caches()
