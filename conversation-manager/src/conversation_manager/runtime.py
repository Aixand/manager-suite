"""Worker ownership selection; conversation protocols remain in the worker."""

from copy import deepcopy
from pathlib import Path
import shutil
import unicodedata

from .models import ConversationError, reject_credentials, canonical


def process_options(value=None):
    value = deepcopy({} if value is None else value)
    canonical(value)
    if not isinstance(value, dict) or set(value) - {"name", "labels", "metadata"}:
        raise ConversationError(
            "validation", "Process options accept name, labels and metadata"
        )
    if "name" in value and (
        not isinstance(value["name"], str)
        or not value["name"].strip()
        or value["name"] != value["name"].strip()
        or len(value["name"].encode()) > 128
        or any(unicodedata.category(c) == "Cc" for c in value["name"])
    ):
        raise ConversationError(
            "validation", "Process name must contain 1 to 128 UTF-8 bytes"
        )
    for kind, maximum in [("labels", 256), ("metadata", 4096)]:
        rows = value.get(kind, {})
        if not isinstance(rows, dict) or len(rows) > 56:
            raise ConversationError(
                "validation", "Process attributes must be bounded string mappings"
            )
        for key, item in rows.items():
            if (
                not isinstance(key, str)
                or not key
                or key.startswith("cm.")
                or len(key.encode()) > 64
                or not isinstance(item, str)
                or len(item.encode()) > maximum
                or any(unicodedata.category(char) == "Cc" for char in key + item)
            ):
                raise ConversationError(
                    "validation", "Invalid process attribute or reserved cm. key"
                )
    reject_credentials(value)
    return value


def process_matches(saved, requested):
    """Only explicit per-start attributes constrain an already shared worker."""
    for key, value in (requested or {}).items():
        if key == "name":
            if saved.get(key) != value:
                return False
        elif any(saved.get(key, {}).get(name) != item for name, item in value.items()):
            return False
    return True


def runtime_options(kind="standalone", options=None):
    if kind not in ("standalone", "undercurrent"):
        raise ConversationError(
            "validation", "Runtime must be standalone or undercurrent"
        )
    options = deepcopy({} if options is None else options)
    if not isinstance(options, dict) or set(options) - {
        "home",
        "bridge",
        "request_timeout",
        "request_bytes",
    }:
        raise ConversationError("validation", "Unknown runtime connection options")
    if kind == "standalone":
        if options:
            raise ConversationError(
                "validation", "Standalone runtime takes no daemon options"
            )
        return dict(kind=kind, options={})
    try:
        from undercurrent import Client
    except ImportError as error:
        raise ConversationError(
            "unsupported", "Install aixand-conversation-manager[undercurrent]"
        ) from error
    observed = Client(
        **options
    ).connection_options  # Resolves configuration without connecting.
    executable = shutil.which(str(observed["bridge"]))
    if executable is None:
        raise ConversationError(
            "unsupported",
            "Install a matching uc-python-bridge or provide its absolute path",
        )
    observed["bridge"] = str(Path(executable).resolve())
    observed["home"] = str(Path(observed["home"]).expanduser().resolve())
    return dict(kind=kind, options=observed)


def launcher(client, runtime_ref=None, *, process=None, prepared=None):
    from .launcher import StandaloneLauncher

    if runtime_ref is not None:
        if not isinstance(runtime_ref, dict) or not runtime_ref.get("id"):
            raise ConversationError(
                "validation", "Use an exact saved runtime reference"
            )
        owner = runtime_ref.get("owner")
        if owner == "conversation-manager-standalone":
            return StandaloneLauncher(client.home)
        if owner != "conversation-manager-undercurrent":
            raise ConversationError(
                "unsupported",
                "Use the recorded external process owner for this runtime",
            )
        if not isinstance(runtime_ref.get("connection"), dict):
            raise ConversationError("validation", "Missing recorded daemon connection")
        config = dict(kind="undercurrent", options=runtime_ref["connection"])
    else:
        config = client.runtime
    if config["kind"] == "standalone":
        return StandaloneLauncher(client.home)
    from .managed_launcher import WorkerLauncher
    from .undercurrent_owner import UndercurrentOwner

    context = (
        {}
        if prepared is None
        else {"provider": prepared.backend_id, "connection": prepared.connection_id}
    )
    if prepared is not None:
        environment = (
            prepared.transport.get("agent_environment_ref", {})
            .get("target", {})
            .get("environment_id")
        )
        if environment:
            context["environment"] = environment
    return WorkerLauncher(
        UndercurrentOwner(
            client.home, config["options"], process=process, context=context
        ),
        client.home,
    )
