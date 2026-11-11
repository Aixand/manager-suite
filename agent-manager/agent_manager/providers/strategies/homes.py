"""Native home environment selected by a provider manifest."""
import os
from pathlib import Path

from agent_manager.errors import Error


def environment(manager, home):
    policy = manager.provider_spec.home
    if policy.shared:
        if home.resolve() != manager.native.resolve():
            raise Error("This provider requires its shared native home")
        # Callers cannot claim one shared home while executing against another.
        variables = {"HOME": str(Path.home())}
        if os.name == "nt": variables["USERPROFILE"] = str(Path.home())
        return variables
    if policy.opencode:
        from agent_manager.providers.opencode import environment as xdg_environment
        return xdg_environment(manager, home)
    return {policy.environment: str(home.resolve())}
