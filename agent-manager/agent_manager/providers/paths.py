"""Locations shared by provider preferences and local definitions."""
import os
from pathlib import Path
from agent_manager._paths import data_root


def configuration_path(config=None):
    return Path(config or os.environ.get(
        "AGENT_MANAGER_CONFIG", data_root() / "providers.toml"
    )).expanduser()


def definitions_directory(config=None):
    return configuration_path(config).parent / "providers"
