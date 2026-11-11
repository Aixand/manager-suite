"""Public module identity for live operations.

Alias the module so existing instrumentation and patches observe the same
receipt-writing functions as the canonical implementation.
"""
import sys
from .live import service as _implementation

sys.modules[__name__] = _implementation
