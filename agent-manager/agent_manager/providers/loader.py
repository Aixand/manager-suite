"""Read and validate provider manifests without starting providers or reading auth."""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import tomllib

from agent_manager.providers.schema import ManifestError, parse_definition
from agent_manager.providers.paths import definitions_directory

DEFINITIONS = Path(__file__).with_name("definitions")


def _registry(definitions):
    registry = {}
    for definition in sorted(definitions, key=lambda row: (row.order, row.id)):
        if definition.id in registry:
            raise ManifestError(f"{definition.source}: duplicate provider ID {definition.id}")
        registry[definition.id] = definition
    names = set(registry)
    for definition in registry.values():
        for alias in definition.aliases:
            if alias in names:
                raise ManifestError(f"{definition.source}: duplicate provider name or alias {alias}")
            names.add(alias)
    return registry


def _local_definition(value, path, bundled):
    definition = parse_definition(value, source=path)
    base = bundled.get(definition.id)
    if base is None:
        return definition
    value = deepcopy(value)
    terminal = value["terminal"]
    sessions = base.terminal.get("sessions", {})
    if sessions.get("cross_environment_resume") == "disabled" and terminal.get("sessions", {}).get("cross_environment_resume", "disabled") != "disabled":
        raise ManifestError(f"{path}: cross-profile resume is not qualified for this provider")
    if sessions.get("cross_environment_resume_reason") and "sessions" in terminal:
        terminal["sessions"].setdefault("cross_environment_resume_reason", sessions["cross_environment_resume_reason"])
    if sessions.get("strategy") == "native-latest" and terminal.get("sessions", {}).get("strategy") not in (None, "native-latest"):
        raise ManifestError(f"{path}: exact retained-session continuation is not qualified for this provider")
    if base.terminal["home"]["strategy"] == "native-shared" and terminal["home"]["strategy"] != "native-shared":
        raise ManifestError(f"{path}: this provider requires its shared native home")
    if base.terminal["home"]["strategy"] == "native-shared" and terminal["home"]["default_path"] != base.terminal["home"]["default_path"]:
        raise ManifestError(f"{path}: this provider's shared native home cannot be redirected")
    value["aliases"] = list(dict.fromkeys((*base.aliases, *definition.aliases)))
    return parse_definition(value, source=path)


def _read_directory(directory, *, optional=False, bundled=None):
    directory = Path(directory)
    try:
        if optional and not directory.exists():
            return {}
        if directory.exists() and not directory.is_dir():
            raise ManifestError(f"{directory}: provider definitions must be a directory")
        paths = sorted(directory.glob("*.toml"))
        if not paths:
            if optional:
                return {}
            raise ManifestError(f"{directory}: no provider manifests found")
        definitions = []
        for path in paths:
            try:
                value = tomllib.loads(path.read_text(encoding="utf-8"))
            except (tomllib.TOMLDecodeError, UnicodeError):
                # TOML diagnostics can contain source values. Report the file
                # without echoing potentially sensitive accidental contents.
                raise ManifestError(f"{path}: invalid TOML provider manifest") from None
            definitions.append(_local_definition(value, path, bundled) if bundled is not None
                               else parse_definition(value, source=path))
    except OSError as error:
        raise ManifestError(f"{directory}: could not read provider manifests") from error
    return _registry(definitions)


@lru_cache(maxsize=1)
def _bundled():
    return _read_directory(DEFINITIONS)


def load_definitions(directory=None, *, config=None):
    """Overlay local definitions on shipped defaults, or read a development directory.

    Callers receive independent copies. A preference override never changes the
    cached defaults or constraints used by the next Manager.
    """
    if directory is not None:
        return _read_directory(directory)
    bundled = deepcopy(_bundled())
    local = _read_directory(definitions_directory(config), optional=True, bundled=bundled)
    return _registry((bundled | local).values())


def aliases(*, config=None):
    return {alias: row.id for row in load_definitions(config=config).values() for alias in row.aliases}
