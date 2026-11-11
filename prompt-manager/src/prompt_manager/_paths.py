"""Default product location; explicit product paths take precedence."""
import os
from pathlib import Path


def default_root():
    """Resolve defaults without creating directories or adopting unselected stores."""
    suite = os.environ.get("AIXAND_MANAGER_ROOT")
    if suite:
        return Path(suite).expanduser().absolute() / 'prompt'
    value = os.environ.get("XDG_DATA_HOME", "")
    base = Path(value) if value else None
    if base is None or not base.is_absolute():
        base = Path.home() / ".local/share"
    return base / "aixand/manager" / 'prompt'
