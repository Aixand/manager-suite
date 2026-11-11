"""Plan and execute a fail-closed reset of Agent Manager data and native homes."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tomllib
import uuid

from agent_manager.errors import Error
from agent_manager.profiles.identities import validate
from agent_manager.providers.paths import configuration_path
from agent_manager.storage.platform import check_process
from agent_manager.storage.registry import read_state
from agent_manager.storage.files import atomic


SCHEMA = "agent-manager/reset-plan-2"


@dataclass(frozen=True)
class NativeHome:
    path: Path
    providers: tuple[str, ...]
    ownership: tuple[str, ...]
    default: bool

    def public(self):
        return dict(
            path=str(self.path),
            providers=list(self.providers),
            ownership=list(self.ownership),
            default=self.default,
        )


@dataclass(frozen=True)
class ConfigurationReset:
    provider: str | None
    requested: bool
    config: Path
    preference_keys: tuple[str, ...]
    manifest_paths: tuple[Path, ...]
    replacement: bytes | None

    @property
    def available(self):
        return bool(self.preference_keys or self.manifest_paths)

    def public(self):
        return dict(
            provider=self.provider,
            available=self.available,
            requested=self.requested,
            config=str(self.config),
            preferenceKeys=list(self.preference_keys),
            manifestPaths=list(map(str, self.manifest_paths)),
        )


@dataclass(frozen=True)
class Plan:
    scope: str
    provider: str | None
    config: Path
    root: Path
    manager_paths: tuple[Path, ...]
    homes: tuple[NativeHome, ...]
    configuration_reset: ConfigurationReset
    evidence: tuple[tuple[str, str], ...]
    revision: str

    def public(self):
        return dict(
            schema=SCHEMA,
            revision=self.revision,
            scope=self.scope,
            provider=self.provider,
            config=str(self.config),
            home=str(self.root),
            managerPaths=list(map(str, self.manager_paths)),
            nativeHomes=[home.public() for home in self.homes],
            configurationReset=self.configuration_reset.public(),
        )


def _path(value):
    return Path(os.path.abspath(Path(value).expanduser()))


def _state_root(root=None):
    from agent_manager._paths import state_root
    return _safe(state_root(root))


def _default_context(config, root):
    from agent_manager._paths import data_root
    base = _path(data_root()).resolve(strict=False)
    return config == base / "providers.toml" and root == base / "state", base


def _legacy_installation_state():
    return _path(
        Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
        / "agent-manager"
    )


def _configuration(config):
    path = _safe(configuration_path(config))
    if path.exists():
        from agent_manager.providers.config import load_registry

        registry = load_registry(path)
        return path, registry.definitions, registry.settings, registry.names
    from agent_manager.providers import load_definitions
    from agent_manager.providers.strategies import manager_config

    definitions = load_definitions(config=path)
    names = {alias: row.id for row in definitions.values() for alias in row.aliases}
    return (
        path,
        definitions,
        {key: manager_config(row) for key, row in definitions.items()},
        names,
    )


def _native_default(provider, definition, settings):
    home = _safe(settings[provider]["native_home"])
    if (
        definition.terminal["home"]["strategy"] == "opencode-xdg"
        and settings[provider]["native_home"] == "~/.local/share/opencode"
    ):
        home = _safe(
            Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
            / "opencode"
        )
    return home


def _default_paths(provider, definition, home):
    paths = {home}
    if definition.terminal["home"]["strategy"] == "opencode-xdg":
        config = _path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        cache = _path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        state = _path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
        paths.update((config / "opencode", cache / "opencode", state / "opencode"))
    return paths


def _direct(path):
    if path.is_symlink():
        raise Error(
            "Reset target is a symbolic link; inspect it manually: " + str(path)
        )
    for parent in path.parents:
        if parent.exists() and parent.is_symlink():
            raise Error(
                "Reset target has a symbolic-link ancestor; inspect it manually: "
                + str(path)
            )


def _safe(path):
    path = _path(path)
    _direct(path)
    path = path.resolve(strict=False)
    home = _path(Path.home()).resolve(strict=False)
    protected = tuple(
        _path(item).resolve(strict=False)
        for item in (Path.cwd(), sys.executable, __file__)
    )
    if path == Path(path.anchor) or path == home:
        raise Error(
            "Reset refuses a filesystem root or the user home directory: " + str(path)
        )
    if any(item == path or item.is_relative_to(path) for item in protected):
        raise Error(
            "Reset target contains the current workspace or executable: " + str(path)
        )
    return path


def _existing(paths):
    result = []
    for value in paths:
        path = _safe(value)
        if path.exists() or path.is_symlink():
            result.append(path)
    return result


def _deduplicate(paths):
    result = []
    for path in sorted(set(paths), key=lambda item: (len(item.parts), str(item))):
        if not any(path == parent or path.is_relative_to(parent) for parent in result):
            result.append(path)
    return tuple(result)


def _active(state, provider):
    for run_id, row in state["runs"].items():
        if row.get("runtime") == "undercurrent":
            raise Error(
                f"Stop active or unconfirmed {provider} run {run_id} before resetting"
            )
        try:
            check_process(row["pid"])
        except ProcessLookupError:
            continue
        except (KeyError, TypeError, PermissionError, OSError):
            raise Error(f"Could not prove {provider} run {run_id} is stopped") from None
        raise Error(f"Stop active {provider} run {run_id} before resetting")


def _evidence(paths):
    rows = []
    for path in sorted(set(paths), key=str):
        if path.is_file() and not path.is_symlink():
            rows.append((str(path), hashlib.sha256(path.read_bytes()).hexdigest()))
        else:
            rows.append((str(path), "missing"))
    return tuple(rows)


from agent_manager.storage.toml import _toml_document, _toml_header_path


def _remove_provider_tables(source, preference_keys, expected):
    lines = source.splitlines(keepends=True)
    headers = [
        (index, path)
        for index, line in enumerate(lines)
        if (path := _toml_header_path(line)) is not None
    ]
    removed = set()
    selected = set()
    for position, (start, path) in enumerate(headers):
        if len(path) < 2 or path[0] != "providers" or path[1] not in preference_keys:
            continue
        end = headers[position + 1][0] if position + 1 < len(headers) else len(lines)
        selected.update(range(start, end))
        removed.add(path[1])
    candidate = "".join(
        line for index, line in enumerate(lines) if index not in selected
    ).encode()
    if removed == set(preference_keys):
        try:
            if tomllib.loads(candidate.decode()) == expected:
                return candidate
        except (tomllib.TOMLDecodeError, UnicodeError):
            pass
    return _toml_document(expected)


def _configuration_reset(config_path, definitions, aliases, provider, requested):
    preference_keys = ()
    replacement = None
    if provider is not None and config_path.exists():
        try:
            document = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeError):
            raise Error(f"Invalid provider configuration: {config_path}") from None
        providers = document.get("providers", {})
        if isinstance(providers, dict):
            preference_keys = tuple(
                key for key in providers if aliases.get(key, key) == provider
            )
            if requested and preference_keys:
                for key in preference_keys:
                    del providers[key]
                replacement = _remove_provider_tables(
                    config_path.read_text(encoding="utf-8"),
                    preference_keys,
                    document,
                )
    manifests = []
    if provider is not None:
        source = _safe(definitions[provider].source)
        directory = _safe(config_path.parent / "providers")
        if source.parent == directory and source.exists():
            manifests.append(source)
    return ConfigurationReset(
        provider,
        requested,
        config_path,
        preference_keys,
        tuple(manifests),
        replacement,
    )


def create_plan(*, provider=None, config=None, root=None, scope=None, reset_provider_config=False):
    config_path, definitions, settings, aliases = _configuration(config)
    provider = aliases.get(provider, provider)
    if provider is not None and provider not in definitions:
        raise Error("Unknown provider: " + provider)
    scope = "provider" if provider is not None else scope or "factory"
    if scope not in ("provider", "providers", "factory"):
        raise Error("Unknown reset scope: " + str(scope))
    if provider is not None and scope != "provider":
        raise Error("A provider reset cannot use a global reset scope")
    if provider is None and scope == "provider":
        raise Error("Provider reset scope requires a provider")
    if reset_provider_config and provider is None:
        raise Error("Provider configuration reset requires a specific provider")
    state_root = _state_root(root)
    _direct(config_path)
    _direct(state_root)
    default_context, dedicated_root = _default_context(config_path, state_root)
    evidence_paths = [config_path]
    for directory in (
        config_path.parent / "providers",
        config_path.parent / "services",
    ):
        _direct(directory)
        if directory.is_dir():
            evidence_paths.extend(directory.glob("*.toml"))

    configuration_reset = _configuration_reset(
        config_path, definitions, aliases, provider, reset_provider_config
    )
    homes = {}
    registered_defaults = set()
    if state_root.exists() and not state_root.is_dir():
        raise Error("Agent Manager state root is not a directory: " + str(state_root))
    for state_path in (
        sorted(state_root.glob("*/state.json")) if state_root.exists() else ()
    ):
        state_provider = state_path.parent.name
        if provider is not None and state_provider != provider:
            continue
        if state_provider not in definitions or state_provider not in settings:
            raise Error(
                "Reset cannot classify an unknown provider registry: " + state_provider
            )
        state = validate(read_state(state_path))
        _active(state, state_provider)
        evidence_paths.append(state_path)
        native = _native_default(state_provider, definitions[state_provider], settings)
        for row in state["environments"].values():
            path = _safe(row["home"])
            is_default = path == native
            if is_default:
                registered_defaults.add(state_provider)
            current = homes.setdefault(
                path, dict(providers=set(), ownership=set(), default=False)
            )
            current["providers"].add(state_provider)
            current["ownership"].add(row["ownership"])
            current["default"] = current["default"] or is_default

    if provider is not None:
        default_providers = {provider: definitions[provider]}
    else:
        default_providers = (
            definitions
            if default_context
            else {key: definitions[key] for key in registered_defaults}
        )
    for default_provider, definition in default_providers.items():
        native = _native_default(default_provider, definition, settings)
        for path in _default_paths(default_provider, definition, native):
            path = _safe(path)
            if not path.exists() and not path.is_symlink():
                continue
            current = homes.setdefault(
                path, dict(providers=set(), ownership=set(), default=True)
            )
            current["providers"].add(default_provider)
            current["ownership"].add("native-default")
            current["default"] = True

    if provider is not None:
        from agent_manager._paths import managed_homes_root

        manager_paths = [
            state_root / provider,
            managed_homes_root(state_root) / provider,
            state_root / "installations" / (provider + ".json"),
        ]
        if default_context:
            manager_paths.append(
                _legacy_installation_state() / "installations" / (provider + ".json")
            )
        if configuration_reset.requested:
            manager_paths.extend(configuration_reset.manifest_paths)
    elif scope == "providers":
        from agent_manager._paths import managed_homes_root

        manager_paths = []
        for selected_provider in definitions:
            manager_paths.extend(
                (
                    state_root / selected_provider,
                    managed_homes_root(state_root) / selected_provider,
                    state_root / "installations" / (selected_provider + ".json"),
                )
            )
            if default_context:
                manager_paths.append(
                    _legacy_installation_state()
                    / "installations"
                    / (selected_provider + ".json")
                )
    elif default_context:
        manager_paths = [dedicated_root, _legacy_installation_state()]
    else:
        manager_paths = [
            state_root,
            config_path,
            config_path.parent / "providers",
            config_path.parent / "services",
        ]
    manager_paths = _deduplicate(_existing(manager_paths))
    native_homes = tuple(
        NativeHome(
            path,
            tuple(sorted(row["providers"])),
            tuple(sorted(row["ownership"])),
            row["default"],
        )
        for path, row in sorted(homes.items(), key=lambda item: str(item[0]))
    )
    evidence = _evidence(evidence_paths)
    payload = dict(
        scope=scope,
        provider=provider,
        config=str(config_path),
        root=str(state_root),
        managerPaths=list(map(str, manager_paths)),
        homes=[home.public() for home in native_homes],
        configurationReset=configuration_reset.public(),
        configurationReplacement=(
            hashlib.sha256(configuration_reset.replacement).hexdigest()
            if configuration_reset.replacement is not None
            else None
        ),
        evidence=evidence,
    )
    revision = (
        "sha256:"
        + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    )
    return Plan(
        scope,
        provider,
        config_path,
        state_root,
        manager_paths,
        native_homes,
        configuration_reset,
        evidence,
        revision,
    )


def _targets(plan, include_default_homes):
    preserved = [
        home.path for home in plan.homes if home.default and not include_default_homes
    ]
    selected = [
        *plan.manager_paths,
        *(
            home.path
            for home in plan.homes
            if include_default_homes or not home.default
        ),
    ]
    selected = _deduplicate(map(_safe, selected))
    for parent in selected:
        if any(path == parent or path.is_relative_to(parent) for path in preserved):
            raise Error(
                "Cannot preserve a default native home nested inside reset target: "
                + str(parent)
            )
    return selected


def _erase(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def perform(plan, *, include_default_homes=False):
    targets = _targets(plan, include_default_homes)
    staged = []
    rewritten = None
    try:
        for path in targets:
            if not path.exists() and not path.is_symlink():
                continue
            quarantine = path.with_name(
                "." + path.name + ".agent-manager-reset-" + uuid.uuid4().hex
            )
            if quarantine.exists() or quarantine.is_symlink():
                raise Error("Reset quarantine path already exists: " + str(quarantine))
            path.rename(quarantine)
            staged.append((path, quarantine))
        configuration = plan.configuration_reset
        if configuration.requested and configuration.replacement is not None:
            path = _safe(configuration.config)
            quarantine = path.with_name(
                "." + path.name + ".agent-manager-reset-" + uuid.uuid4().hex
            )
            if quarantine.exists() or quarantine.is_symlink():
                raise Error("Reset quarantine path already exists: " + str(quarantine))
            path.rename(quarantine)
            rewritten = (path, quarantine)
            atomic(path, configuration.replacement)
    except BaseException as error:
        failures = []
        if rewritten is not None:
            path, quarantine = rewritten
            try:
                path.unlink(missing_ok=True)
                quarantine.rename(path)
            except OSError:
                failures.append(str(quarantine))
        for path, quarantine in reversed(staged):
            try:
                quarantine.rename(path)
            except OSError:
                failures.append(str(quarantine))
        if failures:
            raise Error(
                "Reset staging failed and rollback needs manual recovery: "
                + ", ".join(failures)
            ) from error
        raise

    failures = []
    detached = [*staged, *((rewritten,) if rewritten is not None else ())]
    for _, quarantine in detached:
        try:
            _erase(quarantine)
        except OSError:
            failures.append(str(quarantine))
    if failures:
        raise Error("Reset detached data but could not erase: " + ", ".join(failures))
    preserved = [
        str(home.path)
        for home in plan.homes
        if home.default and not include_default_homes
    ]
    return dict(
        schema="agent-manager/reset-result-2",
        reset=True,
        provider=plan.provider,
        scope=plan.scope,
        providerConfigurationReset=plan.configuration_reset.requested,
        defaultHomesDeleted=include_default_homes,
        deleted=[str(path) for path, _ in staged],
        preservedDefaultHomes=preserved,
    )
