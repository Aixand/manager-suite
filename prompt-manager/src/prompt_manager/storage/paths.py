"""Standalone prompt-store location policy."""

import os
from pathlib import Path

from .._paths import default_root


def default_home():
    return Path(
        os.environ.get("PROMPT_MANAGER_HOME")
        or default_root()
    )
