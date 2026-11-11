"""Declared strategies select built-in native implementations."""
from copy import deepcopy
HOME_STRATEGIES = {"environment-directory": None, "opencode-xdg": "opencode", "native-shared": "native"}
SESSION_STRATEGIES = {"codex-transcripts": "codex", "opencode-database": "opencode", "native-picker": "native", "native-latest": "native"}
MODEL_STRATEGIES = {"codex-config", "opencode-config", "native-argument"}


def manager_config(definition):
    terminal = deepcopy(definition.terminal)
    result = {key: terminal[key] for key in ("executable", "runtime", "run_args", "launch_args", "app_server_args", "detection_profile") if key in terminal}
    result.update(label=definition.label, description=definition.description, native_home=terminal["home"]["default_path"])
    return result
