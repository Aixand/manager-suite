"""Declarative provider definitions, independent of runtime and account state."""

from agent_manager.providers.loader import aliases, load_definitions
from agent_manager.providers.schema import ManifestError, ProviderDefinition

__all__ = ["ManifestError", "ProviderDefinition", "aliases", "load_definitions"]
