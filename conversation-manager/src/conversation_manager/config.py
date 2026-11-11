"""Editable application defaults, separate from durable conversation state."""

from copy import deepcopy
import os
import math
from pathlib import Path
import tomllib

from .models import ConversationError, digest, reject_credentials
from .runtime import process_options
from ._paths import default_root

SAMPLE = """# Conversation Manager defaults. Explicit SDK/CLI arguments take precedence.
# Conversation data and retained runtime references are managed separately.
[storage]
# home = "/absolute/path/to/conversation/state"

[runtime]
kind = "standalone" # Or "undercurrent"; requires an existing daemon.

[runtime.undercurrent]
# home = "~/.aixand/undercurrent"
# bridge = "/absolute/path/to/uc-python-bridge"

[process]
# name_prefix = "conversation"

[process.labels]
# app = "conversation-manager"

[process.metadata]
# purpose = "development"
"""


def path(config=None):
    return (
        Path(
            config
            or os.environ.get("CONVERSATION_MANAGER_CONFIG")
            or default_root() / "config.toml"
        )
        .expanduser()
        .resolve()
    )


def _table(value, allowed):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ConversationError(
            "validation",
            "Unknown Conversation Manager configuration field or invalid table",
        )
    return value


def _path(value, location):
    if not isinstance(value, str) or not value.strip():
        raise ConversationError(
            "validation", "Configuration paths must be nonempty strings"
        )
    selected = Path(value).expanduser()
    return str(
        (selected if selected.is_absolute() else location.parent / selected).resolve()
    )


def load(config=None):
    location = path(config)
    try:
        content = location.read_bytes()
    except FileNotFoundError:
        if config is not None or os.environ.get("CONVERSATION_MANAGER_CONFIG"):
            raise ConversationError(
                "not_found",
                "The selected Conversation Manager config file does not exist",
            ) from None
        content = b""
    except OSError as error:
        raise ConversationError(
            "io", "Cannot read Conversation Manager config"
        ) from error
    try:
        data = tomllib.loads(content.decode("utf-8"))
    except (ValueError, UnicodeError) as error:
        raise ConversationError(
            "validation", "Invalid Conversation Manager TOML config"
        ) from error
    _table(data, {"storage", "runtime", "process"})
    reject_credentials(data)
    storage = _table(data.get("storage", {}), {"home"})
    runtime = _table(data.get("runtime", {}), {"kind", "undercurrent"})
    kind = runtime.get("kind", "standalone")
    if kind not in ("standalone", "undercurrent"):
        raise ConversationError(
            "validation", "Configured runtime must be standalone or undercurrent"
        )
    uc = _table(
        runtime.get("undercurrent", {}),
        {"home", "bridge", "request_timeout", "request_bytes"},
    )
    uc = deepcopy(uc)
    for key in ("home", "bridge"):
        if key in uc:
            uc[key] = _path(uc[key], location)
    for key in ("request_timeout", "request_bytes"):
        if key in uc and (
            isinstance(uc[key], bool)
            or not isinstance(uc[key], (int, float))
            or not math.isfinite(uc[key])
            or uc[key] <= 0
        ):
            raise ConversationError(
                "validation", "Runtime request limits must be positive numbers"
            )
    if "request_timeout" in uc and uc["request_timeout"] > 300:
        raise ConversationError(
            "validation", "Runtime request timeout must not exceed 300 seconds"
        )
    if "request_bytes" in uc and (
        not isinstance(uc["request_bytes"], int)
        or uc["request_bytes"] > 2**32 - 1 - 1838
    ):
        raise ConversationError("validation", "Invalid runtime request byte limit")
    process = deepcopy(
        _table(data.get("process", {}), {"name_prefix", "labels", "metadata"})
    )
    prefix = process.pop("name_prefix", None)
    process_options(process)
    if prefix is not None:
        process_options({"name": prefix})
        if len(prefix.encode()) > 64:
            raise ConversationError(
                "validation", "Process name prefix is limited to 64 bytes"
            )
    process = {key: value for key, value in process.items() if value}
    return dict(
        path=str(location),
        exists=location.is_file(),
        home=_path(storage["home"], location)
        if "home" in storage
        else str(default_root() / "state"),
        runtime=kind,
        runtime_options=uc,
        process=process,
        name_prefix=prefix,
    )


def state_home(home=None, config=None, *, settings=None):
    explicit = (
        home
        if home is not None
        else (os.environ.get("CONVERSATION_MANAGER_HOME") or None)
    )
    return (
        Path(explicit).expanduser().resolve()
        if explicit is not None
        else Path((settings if settings is not None else load(config))["home"])
    )


def runtime_defaults(settings, runtime=None, options=None):
    kind = (
        runtime
        if runtime is not None
        else os.environ.get("CONVERSATION_MANAGER_RUNTIME", settings["runtime"])
    )
    if kind not in ("standalone", "undercurrent"):
        raise ConversationError(
            "validation", "Runtime must be standalone or undercurrent"
        )
    selected = deepcopy(settings["runtime_options"]) if kind == "undercurrent" else {}
    if kind == "undercurrent":
        for key, variable, fallback in [
            ("home", "CONVERSATION_MANAGER_UNDERCURRENT_HOME", "UNDERCURRENT_HOME"),
            (
                "bridge",
                "CONVERSATION_MANAGER_UNDERCURRENT_BRIDGE",
                "UNDERCURRENT_PYTHON_BRIDGE",
            ),
        ]:
            value = os.environ.get(variable) or os.environ.get(fallback)
            if value:
                selected[key] = value
    if options is not None:
        if not isinstance(options, dict):
            raise ConversationError("validation", "Runtime options must be an object")
        selected.update(deepcopy(options))
    return kind, selected


def process_defaults(client, process, operation_id):
    overrides = process_options(process)
    selected = deepcopy(client.configuration["process"])
    for key in ("labels", "metadata"):
        if key in overrides:
            selected[key] = {**selected.get(key, {}), **overrides[key]}
    if "name" in overrides:
        selected["name"] = overrides["name"]
    elif client.configuration["name_prefix"] is not None:
        selected["name"] = (
            client.configuration["name_prefix"]
            + "-"
            + digest([client.store.store_id, operation_id])[-12:]
        )
    return process_options(selected)


def initialize(config=None):
    from aixand_filesystem import private_directory, private_path

    location = path(config)
    if not location.parent.exists():
        private_directory(location.parent)
    try:
        fd = os.open(location, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return dict(path=str(location), created=False)
    with os.fdopen(fd, "w") as stream:
        private_path(location)
        stream.write(SAMPLE)
        stream.flush()
        os.fsync(stream.fileno())
    return dict(path=str(location), created=True)
