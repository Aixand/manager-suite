"""Captured continuation identities; selection performs no native preparation."""
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from collections.abc import Mapping

from agent_manager.profiles.identities import Target


@dataclass(frozen=True)
class ContinuationRequest:
    provider: str
    adapter_id: str
    source: Mapping
    target: Target
    home: Path
    logical: bool = False

    def __post_init__(self):
        object.__setattr__(self, 'source', MappingProxyType(deepcopy(dict(self.source))))


@dataclass(frozen=True)
class ContinuationPlan:
    request: ContinuationRequest
    session: Mapping
    history_mode: str

    def __post_init__(self):
        object.__setattr__(self, 'session', MappingProxyType(deepcopy(dict(self.session))))

    @property
    def target(self): return self.request.target

    @property
    def native_id(self): return self.session['native_id']

    @property
    def receipt(self):
        return dict(schema=1, adapter=self.request.adapter_id, history_mode=self.history_mode,
                    requested_session_ref=self.request.source['ref'], session_ref=self.session['ref'],
                    origin_environment_id=self.request.source['origin_environment_id'],
                    environment_id=self.target.environment_id, native_id=self.native_id)
