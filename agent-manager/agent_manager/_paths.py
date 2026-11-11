"""Stable package and user-data locations."""
import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
POLICY_ROOT = PACKAGE_ROOT / 'providers'


def data_root():
    from ._default_paths import default_root
    return default_root()


def state_root(root=None):
    return Path(root or os.environ.get('AGENT_MANAGER_HOME', data_root() / 'state')).expanduser().absolute()


def managed_homes_root(root=None):
    """Keep custom state contexts self-contained; split the default data layout."""
    override = os.environ.get('AGENT_MANAGER_HOMES_ROOT')
    if override:
        return Path(override).expanduser().absolute()
    selected = state_root(root)
    default = (data_root() / 'state').absolute()
    return (data_root() / 'home') if selected == default else (selected / 'home')
