"""Resolve provider configuration consistently for discovery and Manager."""
from dataclasses import dataclass
import os
from pathlib import Path
import tomllib

from agent_manager.errors import Error
from agent_manager.providers import ManifestError, load_definitions
from agent_manager.providers.strategies import manager_config
from agent_manager.providers.paths import configuration_path
from agent_manager.providers.resolved import resolve_provider
from agent_manager.validation import name
from agent_manager.services.loader import load_services
from agent_manager.services.schema import ServiceError, fail


@dataclass(frozen=True)
class ProviderRegistry:
    definitions: dict
    settings: dict
    names: dict
    config_path: Path | None
    services: dict

    def resolve(self, provider):
        provider = self.names.get(provider, provider)
        name(provider)
        if provider not in self.settings:
            raise Error(f"Unknown provider: {provider}")
        try:
            return resolve_provider(provider, self.definitions.get(provider), self.settings[provider],
                                    services=self.services, definitions=self.definitions)
        except ServiceError as error: raise Error(str(error)) from None


def load_registry(config=None, *, allow_missing=False):
    config_path = configuration_path(config)
    try:
        definitions = load_definitions(config=config)
        services = load_services(config=config)
        for row in definitions.values():
            if row.service is not None and row.service not in services:
                fail(row.source, 'unknown service reference')
            for key in row.terminal.get('integrations', {}).get('services', ()):
                if key not in services: fail(row.source, 'unknown integration service reference')
    except (ManifestError, ServiceError) as error:
        raise Error(str(error)) from None
    defaults = {key: manager_config(value) for key, value in definitions.items()}
    names = {alias: row.id for row in definitions.values() for alias in row.aliases}
    if config_path.exists():
        try:
            document = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeError):
            raise Error(f"Invalid provider configuration: {config_path}") from None
        custom = document.get("providers", {})
        if not isinstance(custom, dict):
            raise Error("Provider configuration requires a providers table")
        for key, value in custom.items():
            key = names.get(key, key)
            if not isinstance(value, dict):
                raise Error(f"Provider configuration for {key} must be a table")
            if key not in defaults:
                raise Error(f"Provider {key} requires a manifest in the providers directory")
            allowed = {"executable", "runtime", "native_home", "run_args", "launch_args", "app_server_args", "detection_profile", "labels", "metadata"}
            unknown = value.keys() - allowed
            if unknown:
                raise Error(f"Unsupported profile-only preferences for {key}: " + ", ".join(sorted(unknown)))
            defaults[key] = defaults.get(key, {}) | value
    elif not allow_missing and (config or os.environ.get("AGENT_MANAGER_CONFIG")):
        raise Error("The specified provider configuration does not exist")
    return ProviderRegistry(definitions, defaults, names,
                            config_path.absolute() if config_path.exists() else None, services)


def load(config=None):
    registry = load_registry(config)
    return registry.settings, registry.config_path
