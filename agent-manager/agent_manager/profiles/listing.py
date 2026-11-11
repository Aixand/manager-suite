"""List profiles using public metadata only; no implicit login checks."""
from agent_manager.profiles.operations import inspect_profile


def list_profiles(manager, *, model=None):
    return [inspect_profile(manager, profile) for profile in sorted(manager.state()["profiles"])]
