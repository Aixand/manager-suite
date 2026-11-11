"""Strict profile-only provider manifest schema.

Native credentials remain owned by the provider. Preferences and release policy
have separate schemas.
"""
from copy import deepcopy
from dataclasses import dataclass
import re

from agent_manager.providers.strategies import HOME_STRATEGIES, MODEL_STRATEGIES, SESSION_STRATEGIES

SCHEMA_VERSION = 3
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")
_ENVIRONMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class ManifestError(ValueError):
    """A provider definition cannot be interpreted safely or unambiguously."""


@dataclass(frozen=True)
class ProviderDefinition:
    id: str
    label: str
    description: str
    aliases: tuple[str, ...]
    order: int
    terminal: dict
    source: str
    service: str | None = None


def _fail(path, message):
    raise ManifestError(f"{path}: {message}")


def _table(value, path, *, required=(), optional=()):
    if not isinstance(value, dict):
        _fail(path, "must be a table")
    unknown = value.keys() - set(required) - set(optional)
    if unknown:
        _fail(path, "unknown field(s): " + ", ".join(sorted(unknown)))
    missing = set(required) - value.keys()
    if missing:
        _fail(path, "missing field(s): " + ", ".join(sorted(missing)))
    return value


def _string(value, path, *, choices=None, pattern=None):
    if not isinstance(value, str) or not value.strip() or any(c in value for c in ("\0", "\r", "\n")):
        _fail(path, "must be a nonempty single-line string")
    if choices is not None and value not in choices:
        _fail(path, "must be one of: " + ", ".join(sorted(choices)))
    if pattern is not None and pattern.fullmatch(value) is None:
        _fail(path, "has an invalid identifier")


def _strings(value, path, *, identifiers=False):
    if not isinstance(value, list):
        _fail(path, "must be an array of strings")
    for index, item in enumerate(value):
        # Arguments may be empty or multiline; they are always literal argv,
        # never shell code. NUL cannot be passed to a child process.
        if not isinstance(item, str) or "\0" in item:
            _fail(f"{path}[{index}]", "must be a string without NUL")
        if identifiers:
            _string(item, f"{path}[{index}]", pattern=_NAME)
    if identifiers and len(set(value)) != len(value):
        _fail(path, "must not contain duplicate identifiers")


def _home(home, path, provider):
    _table(home, path, required=("strategy", "default_path", "binding", "settings_scope", "history_scope"), optional=("environment",))
    _string(home["strategy"], path + ".strategy", choices=HOME_STRATEGIES)
    _string(home["default_path"], path + ".default_path")
    for key in ("binding", "settings_scope", "history_scope"):
        _string(home[key], path + "." + key, choices=("environment", "shared"))
    binding = {"native-shared": "shared"}.get(home["strategy"], "environment")
    if any(home[key] != binding for key in ("binding", "settings_scope", "history_scope")):
        _fail(path, "scopes must match the selected directory strategy")
    if home["strategy"] == "native-shared":
        if "environment" in home:
            _fail(path, "native-shared cannot set an environment override")
    elif "environment" not in home:
        _fail(path + ".environment", "is required for this directory strategy")
    else:
        _string(home["environment"], path + ".environment", pattern=_ENVIRONMENT)
        environment = {"opencode-xdg": "XDG_DATA_HOME"}.get(home["strategy"])
        if environment and home["environment"] != environment:
            _fail(path + ".environment", "must match the selected directory strategy")


def _login(login, path):
    _table(login, path, required=("args",), optional=("status_args", "logout_args"))
    for key, value in login.items():
        _strings(value, path + "." + key)


def _instructions(value, path, terminal):
    _table(value, path, required=('strategy', 'filename'), optional=('overrides', 'paths'))
    _string(value['strategy'], path + '.strategy', choices=('home', 'opencode-config', 'global'))
    filename = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*\.md')
    _string(value['filename'], path + '.filename', pattern=filename)
    _strings(value.get('overrides', []), path + '.overrides')
    if (len(set(value.get('overrides', []))) != len(value.get('overrides', []))
            or value['filename'] in value.get('overrides', [])):
        _fail(path + '.overrides', 'requires unique filenames distinct from the primary filename')
    for name in value.get('overrides', []): _string(name, path + '.overrides', pattern=filename)
    paths = value.get('paths', [])
    _strings(paths, path + '.paths')
    if len(set(paths)) != len(paths): _fail(path + '.paths', 'requires unique paths')
    from pathlib import PurePosixPath
    for item in paths:
        if (not item.startswith(('~/', '/')) or '..' in PurePosixPath(item).parts
                or '\\' in item or not filename.fullmatch(PurePosixPath(item).name)):
            _fail(path + '.paths', 'requires absolute or home-relative Markdown paths')
    if value['strategy'] == 'global' and not paths:
        _fail(path + '.paths', 'global instructions require explicit paths')
    if value['strategy'] == 'global' and value.get('overrides'):
        _fail(path + '.overrides', 'global instructions use explicit path order')
    if value['strategy'] != 'global' and paths:
        _fail(path + '.paths', 'only global instructions accept paths')
    if value['strategy'] == 'opencode-config' and terminal['home']['strategy'] != 'opencode-xdg':
        _fail(path + '.strategy', 'requires the OpenCode home strategy')


def _sessions(sessions, path):
    _table(sessions, path, required=("strategy", "resume_args", "cross_environment_resume"), optional=(
        "activity_glob", "resume_label", "cross_environment_resume_reason"))
    _string(sessions["strategy"], path + ".strategy", choices=SESSION_STRATEGIES)
    _strings(sessions["resume_args"], path + ".resume_args")
    if not sessions["resume_args"]:
        _fail(path + ".resume_args", "must contain a resume command or flag")
    from agent_manager.runtime.continuation.registry import mechanisms
    _string(sessions["cross_environment_resume"], path + ".cross_environment_resume", choices=("disabled", *mechanisms()))
    if "cross_environment_resume_reason" in sessions:
        _string(sessions["cross_environment_resume_reason"], path + ".cross_environment_resume_reason")
        if sessions["cross_environment_resume"] != "disabled":
            _fail(path + ".cross_environment_resume_reason", "only applies when cross-profile resume is disabled")
    for key in ("activity_glob", "resume_label"):
        if key in sessions:
            _string(sessions[key], path + "." + key)


def _options(terminal, path, provider):
    if "models" in terminal:
        models = _table(terminal["models"], path + ".models", required=("strategy",), optional=('discovery',))
        _string(models["strategy"], path + ".models.strategy", choices=MODEL_STRATEGIES)
        if (models["strategy"] == "opencode-config") != (terminal["home"]["strategy"] == "opencode-xdg"):
            _fail(path + ".models.strategy", "OpenCode model settings require the OpenCode directory strategy")
        if 'discovery' in models:
            from agent_manager.models.adapters import PROVIDERS
            discovery = _table(models['discovery'], path + '.models.discovery', required=('adapter',),
                optional=('versions', 'timeout', 'cache_ttl'))
            _string(discovery['adapter'], path + '.models.discovery.adapter', choices=('disabled', *PROVIDERS))
            if discovery['adapter'] != 'disabled' and PROVIDERS[discovery['adapter']] != provider:
                _fail(path + '.models.discovery.adapter', 'reader must match the provider')
            versions = discovery.get('versions', [])
            _strings(versions, path + '.models.discovery.versions')
            if (len(set(versions)) != len(versions) or any(re.fullmatch(r'\d+\.\d+\.\d+', v) is None for v in versions)
                    or discovery['adapter'] != 'disabled' and not versions):
                _fail(path + '.models.discovery.versions', 'requires unique exact qualified CLI versions')
            for key, default, maximum in (('timeout', 30, 120), ('cache_ttl', 86400, 86400)):
                number = discovery.get(key, default)
                if type(number) is not int or not 1 <= number <= maximum:
                    _fail(path + '.models.discovery.' + key, f'must be an integer between 1 and {maximum}')


def _budget(value, path, provider):
    from agent_manager.budgets.adapters import PROVIDERS
    _table(value, path, required=('adapter',), optional=('versions', 'timeout', 'cache_ttl', 'reason', 'selection'))
    _string(value['adapter'], path + '.adapter', choices=('disabled', *PROVIDERS))
    if value['adapter'] != 'disabled' and PROVIDERS[value['adapter']] != provider:
        _fail(path + '.adapter', 'reader must match the provider')
    versions = value.get('versions', [])
    _strings(versions, path + '.versions')
    if (len(versions) != len(set(versions)) or any(re.fullmatch(r'\d+\.\d+\.\d+', v) is None for v in versions)
            or value['adapter'] != 'disabled' and not versions):
        _fail(path + '.versions', 'requires unique exact qualified CLI versions')
    for key, default, maximum in (('timeout', 20, 120), ('cache_ttl', 300, 3600)):
        number = value.get(key, default)
        if type(number) is not int or not 1 <= number <= maximum:
            _fail(path + '.' + key, f'must be an integer between 1 and {maximum}')
    if 'reason' in value: _string(value['reason'], path + '.reason')
    if 'selection' in value:
        selection = _table(value['selection'], path + '.selection', required=('strategy',), optional=('buckets', 'model_families'))
        _string(selection['strategy'], path + '.selection.strategy', choices=('default', 'max-remaining', 'comparable-or-default'))
        patterns = selection.get('buckets', [])
        _strings(patterns, path + '.selection.buckets')
        if selection['strategy'] == 'max-remaining' and (value['adapter'] == 'disabled' or not patterns):
            _fail(path + '.selection', 'budget ranking requires an enabled reader and bucket patterns')
        if selection['strategy'] == 'comparable-or-default' and (provider != 'opencode' or not patterns):
            _fail(path + '.selection', 'connection ranking requires OpenCode and bucket patterns')
        for pattern in patterns:
            _string(pattern, path + '.selection.buckets', pattern=re.compile(r'[A-Za-z0-9*?._:/-]{1,256}'))
        families = selection.get('model_families', {})
        if not isinstance(families, dict): _fail(path + '.selection.model_families', 'must be a table')
        for family, choices in families.items():
            _string(family, path + '.selection.model_families', pattern=_NAME)
            _strings(choices, path + '.selection.model_families.' + family)
            if not choices: _fail(path + '.selection.model_families.' + family, 'requires model patterns')
            for pattern in choices: _string(pattern, path + '.selection.model_families.' + family)


def _integrations(value, path, provider):
    from agent_manager.integrations.policy import ADAPTER
    _table(value, path, required=('adapter',), optional=('versions', 'timeout', 'cache_ttl', 'services'))
    _string(value['adapter'], path + '.adapter', choices=('disabled', ADAPTER))
    if value['adapter'] != 'disabled' and provider != 'opencode':
        _fail(path + '.adapter', 'integration discovery requires OpenCode')
    versions = value.get('versions', [])
    _strings(versions, path + '.versions')
    if (len(set(versions)) != len(versions) or any(re.fullmatch(r'\d+\.\d+\.\d+', v) is None for v in versions)
            or value['adapter'] != 'disabled' and not versions):
        _fail(path + '.versions', 'requires unique exact qualified CLI versions')
    for key, default, maximum in (('timeout', 30, 120), ('cache_ttl', 300, 300)):
        number = value.get(key, default)
        if type(number) is not int or not 1 <= number <= maximum:
            _fail(path + '.' + key, f'must be an integer between 1 and {maximum}')
    if 'services' in value:
        _strings(value['services'], path + '.services', identifiers=True)
        if len(value['services']) > 128: _fail(path + '.services', 'too many service references')


def parse_definition(value, *, source="<manifest>"):
    """Validate a parsed TOML object; diagnostics name fields, never values."""
    root = str(source)
    _table(value, root, required=("schema_version", "id", "label", "description", "aliases", "terminal"), optional=("order", "service"))
    if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
        _fail(root + ".schema_version", f"only version {SCHEMA_VERSION} is supported")
    _string(value["id"], root + ".id", pattern=_NAME)
    if 'service' in value: _string(value['service'], root + '.service', pattern=_NAME)
    for key in ("label", "description"):
        _string(value[key], root + "." + key)
    _strings(value["aliases"], root + ".aliases", identifiers=True)
    order = value.get("order", 100)
    if type(order) is not int or order < 0:
        _fail(root + ".order", "must be a nonnegative integer")
    path = root + ".terminal"
    terminal = _table(value["terminal"], path, required=("executable", "home", "login"), optional=(
        "runtime", "run_args", "launch_args", "app_server_args", "detection_profile", "selection",
        "sessions", "models", "budget", "integrations", "instructions"))
    _string(terminal["executable"], path + ".executable")
    if "runtime" in terminal:
        _string(terminal["runtime"], path + ".runtime", choices=("direct", "undercurrent"))
    for key in ("run_args", "launch_args", "app_server_args"):
        if key in terminal:
            _strings(terminal[key], path + "." + key)
    if "detection_profile" in terminal:
        _string(terminal["detection_profile"], path + ".detection_profile", pattern=_NAME)
    if "selection" in terminal:
        _string(terminal["selection"], path + ".selection", choices=("default",))
    _home(terminal["home"], path + ".home", value["id"])
    _login(terminal["login"], path + ".login")
    if 'instructions' in terminal: _instructions(terminal['instructions'], path + '.instructions', terminal)
    if "sessions" in terminal:
        _sessions(terminal["sessions"], path + ".sessions")
    from agent_manager.runtime.continuation.registry import definition_error
    problem = definition_error(value['id'], terminal)
    if problem: _fail(path + '.sessions.cross_environment_resume', problem)
    _options(terminal, path, value["id"])
    if 'budget' in terminal: _budget(terminal['budget'], path + '.budget', value['id'])
    if 'integrations' in terminal: _integrations(terminal['integrations'], path + '.integrations', value['id'])
    if (terminal.get('budget', {}).get('selection', {}).get('strategy') == 'comparable-or-default'
            and terminal.get('integrations', {}).get('adapter', 'disabled') == 'disabled'):
        _fail(path + '.budget.selection', 'connection ranking requires an enabled integration reader')
    return ProviderDefinition(value["id"], value["label"], value["description"], tuple(value["aliases"]), order, deepcopy(terminal), root, value.get('service'))
