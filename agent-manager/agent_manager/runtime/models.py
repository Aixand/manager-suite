"""Shared managed-run identities and launch errors."""
from __future__ import annotations
from dataclasses import dataclass, field
from agent_manager.errors import Error
from agent_manager.runtime.handle import Operations

@dataclass(frozen=True)
class Agent(Operations):
    """Immutable execution identity and bound Undercurrent handle from Client."""
    run_id: str
    provider: str
    profile: str
    home: str
    session_id: str
    thread_id: str | None = None
    launch_kind: str = "fresh"
    labels: dict = field(default_factory=dict)
    environment_id: str | None = None
    profile_id: str | None = None
    home_id: str | None = None


class LaunchError(Error):
    def __init__(self, message, run_id):
        super().__init__(f"{message} (run_id={run_id}; use get/reconcile to recover)")
        self.run_id = run_id


class RunNotFoundError(Error):
    """No managed launch intent or retained record exists for this run ID."""

