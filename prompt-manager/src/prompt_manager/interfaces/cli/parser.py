"""Standalone command-line grammar."""

import argparse

from ..._version import __version__
from . import applications, library, managed, sources, store


def parser():
    root = argparse.ArgumentParser(
        prog="prompt-manager", description="Manage local, versioned prompt packs"
    )
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--home", help="Release store (or PROMPT_MANAGER_HOME)")
    root.add_argument(
        "--packs", action="append", help="Draft pack or directory of packs; repeatable"
    )
    commands = root.add_subparsers(dest="command", required=True)
    managed.install(commands)
    library.install(commands)
    sources.install(commands)
    applications.install(commands)
    store.install(commands)
    return root
