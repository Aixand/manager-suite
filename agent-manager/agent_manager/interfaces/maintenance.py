"""Public worker boundary for native provider maintenance and authentication."""
from copy import deepcopy

from agent_manager.errors import Error


def bound(manager, arguments):
    if not isinstance(arguments, dict):
        raise TypeError("Provider operation must be a dictionary")
    result = deepcopy(arguments)
    if result.get("provider", manager.provider) != manager.provider:
        raise Error("Provider operation belongs to another provider")
    for key, expected in (("config", str(manager.config_path) if manager.config_path else None),
                          ("home", str(manager.root.parent.resolve()))):
        if key in result and result[key] != expected:
            raise Error("Provider operation belongs to another manager registry")
        result[key] = expected
    result["provider"] = manager.provider
    return result


def prepare(manager, arguments):
    from agent_manager.runtime.maintenance import validate_operation
    result = bound(manager, arguments)
    if result.get("kind") in ("profile-login", "check-profile"):
        from agent_manager.storage.locks import locks
        from agent_manager.profiles.operations import inspect_profile
        with locks(manager.lock_root, "registry", wait=True):
            validate_operation(manager, result)
            profile = inspect_profile(manager, result["profile"])
            result["expectedRevision"] = profile["environment_revision"]
    else:
        validate_operation(manager, result)
    if result["kind"] in ("install", "update", "repair"):
        from agent_manager.providers.installation.plans import resolve
        result["installationPlan"] = resolve(
            manager, result["kind"], method=result.get("method"),
            version_target=result.get("versionTarget"), reinstall=result.get("reinstall", False),
        )
    return result


def perform(manager, operation, *, progress=None):
    from agent_manager.runtime.maintenance import perform as execute
    result = bound(manager, operation)
    if result.get("kind") in ("profile-login", "check-profile"):
        if not result.get("environment_id") or not result.get("profile_id") or not result.get("expectedRevision"):
            raise Error("Prepare the profile operation before dispatch")
    elif result.get("kind") in ("install", "update", "repair") and not result.get("installationPlan"):
        raise Error("Prepare the installation plan before dispatch")
    return execute(result, progress=progress if progress is not None else lambda value: None)
