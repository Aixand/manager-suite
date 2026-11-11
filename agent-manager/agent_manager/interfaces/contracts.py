"""Versioned public SDK values; no runtime connection or provider SDK imports."""
from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields
from typing import Any, ClassVar, TypedDict

from agent_manager.errors import Error

SDK_CONTRACT_VERSION = 1


class BudgetObservation(TypedDict, total=False):
    schema: str
    provider: str
    environment_id: str
    scope: str
    status: str
    observed_at: float | None
    checked_at: float | None
    inspected_at: float
    cached: bool
    profiles: list[str]
    source: str
    source_revision: str
    cwd: str
    selected_model: str | None
    reason: str | None
    buckets: list[dict]
    error: dict | None


class SavedSession(TypedDict, total=False):
    ref: str
    provider: str
    native_id: str
    history_home: str
    origin_environment_id: str
    cwd: str
    title: str
    updated: float
    conversation_id: str
    rollout_path: str
    history_mode: str
    archived: bool
    parent_id: str | None


@dataclass(frozen=True)
class ExecutionReference:
    schema: ClassVar[str] = 'agent-manager/execution-ref-1'
    provider: str
    registry_path: str
    run_id: str
    runtime_home: str
    session_id: str

    def __post_init__(self):
        if any(not isinstance(value, str) or not value or '\0' in value for value in asdict(self).values()):
            raise Error('Execution reference fields must be nonempty strings')

    def to_dict(self): return dict(schema=self.schema, **asdict(self))

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or value.get('schema') != cls.schema:
            raise Error('Unsupported execution reference schema')
        if set(value) != {'schema', 'provider', 'registry_path', 'run_id', 'runtime_home', 'session_id'}:
            raise Error('Invalid execution reference fields')
        return cls(**{key: item for key, item in value.items() if key != 'schema'})


@dataclass(frozen=True)
class ExecutionState:
    reference: ExecutionReference
    lifecycle: str
    execution: str
    interaction: str
    constraint: str
    health: str
    revision: int
    observed_at: float
    snapshot: Any = field(repr=False, compare=False)
    source: ClassVar[str] = 'undercurrent'
    schema: ClassVar[str] = 'agent-manager/execution-state-1'

    def to_dict(self):
        return dict(schema=self.schema, source=self.source, reference=self.reference.to_dict(),
            **{key: getattr(self, key) for key in ('lifecycle', 'execution', 'interaction',
                'constraint', 'health', 'revision', 'observed_at')})


@dataclass(frozen=True)
class ExecutionUpdate:
    state: ExecutionState
    resynchronized: bool = False


@dataclass(frozen=True)
class SelectionDecision:
    provider: str
    profile: str
    profile_id: str
    environment_id: str
    environment_revision: str
    reason: str
    model: str | None = None
    remaining_percent: float | None = None
    unlimited: bool = False
    budget: BudgetObservation | None = None
    schema: ClassVar[str] = 'agent-manager/selection-1'

    def to_dict(self): return dict(schema=self.schema, **deepcopy(asdict(self)))

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or value.get('schema') != cls.schema:
            raise Error('Unsupported selection schema')
        values = deepcopy({key: item for key, item in value.items() if key != 'schema'})
        if set(values) != {item.name for item in fields(cls)}: raise Error('Invalid selection fields')
        return cls(**values)

    @classmethod
    def capture(cls, manager, decision):
        return cls(manager.provider, str(decision.target), decision.target.profile_id,
            decision.target.environment_id, decision.environment_revision, decision.reason, decision.model,
            decision.score.remaining_percent if decision.score else None,
            decision.score.unlimited if decision.score else False, deepcopy(decision.budget))

    def resolve(self, manager):
        from agent_manager.profiles.identities import for_environment
        from agent_manager.profiles.selection import Decision
        if self.provider != manager.provider: raise Error('Selection belongs to another provider')
        target = for_environment(manager.state(), self.environment_id,
            profile_id=self.profile_id, display=self.profile)
        return Decision(target, self.environment_revision, self.reason, self.model, deepcopy(self.budget))
