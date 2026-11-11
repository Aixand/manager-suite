"""Compatibility module identity for the standalone CLI."""
import sys
from .interfaces.cli import application as _implementation

sys.modules[__name__] = _implementation
