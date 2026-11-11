"""Standalone MCP definitions, retained preparation and connector operations."""
from ._version import __version__

from .errors import McpError
from .manager import AUTO, PREFIX, PRESETS, Manager, definition, digest, names, probe
from .live.contracts import McpConnector, McpTarget, McpCapabilities, McpInventory, McpReconnectResult

__all__ = ["AUTO", "PREFIX", "PRESETS", "Manager", "McpError", "definition", "digest", "names", "probe",
           "McpConnector", "McpTarget", "McpCapabilities", "McpInventory", "McpReconnectResult"]
