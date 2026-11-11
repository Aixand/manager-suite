"""Explicit per-skill handles over public Manager operations; no lifecycle policy."""

from copy import deepcopy
from dataclasses import dataclass, field
from os import PathLike
from typing import Any, ClassVar

from .contracts import (
    ComparisonResult,
    ExportResult,
    FileChanges,
    ManagedResolution,
    ManagedState,
    ManagedStatus,
    ManifestFile,
    ProjectOperation,
    ProjectConnections,
    ProjectPlan,
    ReconciliationPreview,
    ReconciliationSide,
    ReleaseRecord,
    SkillManifest,
    SkillReference,
    ValidationResult,
    file_inputs,
)
from .errors import require, sdk_operation


def _bound(owner):
    require(isinstance(owner.reference, SkillReference), "Expected a SkillReference")
    require(
        str(owner._manager.home) == owner.reference.store,
        "Skill reference belongs to another store",
        "conflict",
    )


@dataclass(frozen=True)
class Project:
    """An explicit project scope. Plans pin package revisions and observed files."""

    _manager: Any = field(repr=False, compare=False)
    root: str
    _store: str = field(repr=False)

    def _check(self):
        require(
            str(self._manager.home) == self._store,
            "Project belongs to another store",
            "conflict",
        )

    @sdk_operation
    def preview_connect(
        self, selections, *, skills_dir: str | None = None, adopt: bool = False
    ) -> ProjectPlan:
        self._check()
        return self._manager.preview_project_connect(
            self.root, selections, skills_dir=skills_dir, adopt=adopt
        )

    @sdk_operation
    def connect(self, plan: ProjectPlan) -> ProjectOperation:
        self._check()
        require(
            isinstance(plan, dict) and plan.get("action") in ("connect", "restore"),
            "Expected a connection/restore plan",
        )
        return self._manager.apply_project_plan(self.root, plan)

    @sdk_operation
    def apply(self, plan: ProjectPlan) -> ProjectOperation:
        self._check()
        return self._manager.apply_project_plan(self.root, plan)

    @sdk_operation
    def preview_update(self, selections=None) -> ProjectPlan:
        self._check()
        return self._manager.preview_project_update(self.root, selections)

    @sdk_operation
    def apply_update(self, plan: ProjectPlan) -> ProjectOperation:
        self._check()
        require(
            isinstance(plan, dict) and plan.get("action") == "update",
            "Expected an update plan",
        )
        return self._manager.apply_project_plan(self.root, plan)

    @sdk_operation
    def preview_disconnect(self, selections, *, forget: bool = False) -> ProjectPlan:
        self._check()
        return self._manager.preview_project_disconnect(
            self.root, selections, forget=forget
        )

    @sdk_operation
    def disconnect(self, selections, *, forget: bool = False) -> ProjectOperation:
        return self.apply(self.preview_disconnect(selections, forget=forget))

    @sdk_operation
    def preview_restore(self, *, adopt: bool = False) -> ProjectPlan:
        self._check()
        return self._manager.preview_project_restore(self.root, adopt=adopt)

    @sdk_operation
    def connections(self) -> ProjectConnections:
        self._check()
        return self._manager.project_connections(self.root)

    @sdk_operation
    def operations(self) -> list[ProjectOperation]:
        self._check()
        return self._manager.project_operations(self.root)

    @sdk_operation
    def recover(self, identifier: str, *, action: str = "resume") -> ProjectOperation:
        self._check()
        return self._manager.recover_project(self.root, identifier, action=action)

    @sdk_operation
    def save_variant(
        self,
        selection: "str | SkillReference | dict[str, str] | Skill | ResolvedSkill",
        variant_id: str,
        *,
        expected_revision: str,
        expected_digest: str,
    ) -> ManagedStatus:
        self._check()
        return self._manager.save_project_variant(
            self.root,
            selection,
            variant_id,
            expected_revision=expected_revision,
            expected_digest=expected_digest,
        )


@dataclass(frozen=True)
class ResolvedSkill:
    """One accepted state. Reads/exports always select this exact historical revision."""

    _manager: Any = field(repr=False, compare=False)
    reference: SkillReference
    _record: ManagedResolution = field(repr=False)

    def __post_init__(self):
        _bound(self)
        value = deepcopy(self._record)
        require(
            isinstance(value, dict)
            and value.get("schema") == "skill-manager/managed-resolution-1"
            and isinstance(value.get("resolution"), dict),
            "Expected a native skill resolution",
        )
        state = value["resolution"]
        require(
            value.get("store") == self.reference.store
            and state.get("publisher_id") == self.reference.publisher_id
            and state.get("id") == self.reference.skill_id
            and value.get("digest") == state.get("active_digest"),
            "Resolved skill identity mismatch",
            "conflict",
        )
        object.__setattr__(self, "_record", value)

    @property
    def name(self) -> str:
        return self._record["name"]

    @property
    def digest(self) -> str:
        return self._record["digest"]

    @property
    def revision(self) -> str:
        return self._record["resolution"]["revision"]

    @property
    def status(self) -> ManagedStatus:
        return deepcopy(self._record["resolution"])

    @property
    def manifest(self) -> SkillManifest:
        return deepcopy(self._record["manifest"])

    @property
    def metadata(self) -> dict[str, Any]:
        return deepcopy(self._record["manifest"]["metadata"])

    @property
    def files(self) -> list[ManifestFile]:
        return deepcopy(self._record["manifest"]["files"])

    @property
    def directories(self) -> list[str]:
        return list(self._record["manifest"]["directories"])

    def to_dict(self) -> ManagedResolution:
        return deepcopy(self._record)

    @sdk_operation
    def read_file(self, path: str) -> bytes:
        _bound(self)
        return self._manager.read_managed_file(
            self.reference.publisher_id,
            self.reference.skill_id,
            path,
            revision=self.revision,
        )

    @sdk_operation
    def export(self, destination: str | PathLike) -> ExportResult:
        _bound(self)
        return self._manager.export_managed(
            self.reference.publisher_id,
            self.reference.skill_id,
            destination,
            revision=self.revision,
        )

    @sdk_operation
    def release(self, version: str) -> ReleaseRecord:
        _bound(self)
        return self._manager.release_managed(
            self.reference.publisher_id,
            self.reference.skill_id,
            version,
            revision=self.revision,
        )


@dataclass(frozen=True)
class RegistrationResult:
    content: ResolvedSkill
    schema: ClassVar[str] = "skill-manager/registration-result-1"

    @property
    def status(self) -> ManagedStatus:
        return self.content.status

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "content": self.content.to_dict(),
            "status": self.status,
        }


@dataclass(frozen=True)
class Skill:
    """Bind an identity without importing content, refreshing, or fetching."""

    _manager: Any = field(repr=False, compare=False)
    reference: SkillReference

    def __post_init__(self):
        _bound(self)

    @sdk_operation
    def register(
        self,
        source: str | PathLike,
        *,
        publisher_revision: int | None = None,
        version: str | None = None,
    ) -> RegistrationResult:
        _bound(self)
        return self._manager.register_skill(
            self.reference.publisher_id,
            source,
            skill_id=self.reference.skill_id,
            publisher_revision=publisher_revision,
            version=version,
        )

    @sdk_operation
    def refresh(
        self,
        source: str | PathLike,
        *,
        publisher_revision: int | None = None,
        version: str | None = None,
    ) -> ManagedStatus:
        _bound(self)
        return self._manager.ensure_registered(
            self.reference.publisher_id,
            source,
            skill_id=self.reference.skill_id,
            publisher_revision=publisher_revision,
            version=version,
        )

    @sdk_operation
    def resolve(self, *, revision: str | None = None) -> ResolvedSkill:
        _bound(self)
        record = self._manager.resolve_managed(
            self.reference.publisher_id, self.reference.skill_id, revision=revision
        )
        return ResolvedSkill(self._manager, self.reference, record)

    @sdk_operation
    def status(self, *, revision: str | None = None) -> ManagedStatus:
        _bound(self)
        return self._manager.managed_status(
            self.reference.publisher_id, self.reference.skill_id, revision=revision
        )

    @sdk_operation
    def save(self, files: FileChanges, *, expected_revision: str) -> ManagedStatus:
        _bound(self)
        return self._manager.save_managed(
            self.reference.publisher_id,
            self.reference.skill_id,
            file_inputs(files),
            expected_revision=expected_revision,
        )

    @sdk_operation
    def validate(
        self, files: FileChanges | None = None, *, revision: str | None = None
    ) -> ValidationResult:
        _bound(self)
        return self._manager.validate_managed(
            self.reference.publisher_id,
            self.reference.skill_id,
            files,
            revision=revision,
        )

    @sdk_operation
    def preview_reconciliation(
        self,
        *,
        resolutions: dict[str, Any] | None = None,
        take: ReconciliationSide | None = None,
    ) -> ReconciliationPreview:
        _bound(self)
        return self._manager.preview_reconciliation(
            self.reference.publisher_id,
            self.reference.skill_id,
            resolutions=resolutions,
            take=take,
        )

    @sdk_operation
    def reconcile(
        self,
        *,
        expected_revision: str,
        resolutions: dict[str, Any] | None = None,
        take: ReconciliationSide | None = None,
    ) -> ManagedStatus:
        _bound(self)
        return self._manager.reconcile(
            self.reference.publisher_id,
            self.reference.skill_id,
            expected_revision=expected_revision,
            resolutions=resolutions,
            take=take,
        )

    @sdk_operation
    def inspect(
        self,
        *,
        review: bool = False,
        path: str | None = None,
        expected_revision: str | None = None,
        resolutions: dict[str, Any] | None = None,
        take: ReconciliationSide | None = None,
        offset: int = 0,
        limit: int = 32768,
        conflict_offset: int = 0,
    ) -> dict[str, Any]:
        _bound(self)
        return self._manager.managed_view(
            self.reference.publisher_id,
            self.reference.skill_id,
            review=review,
            path=path,
            expected_revision=expected_revision,
            resolutions=resolutions,
            take=take,
            offset=offset,
            limit=limit,
            conflict_offset=conflict_offset,
        )

    @sdk_operation
    def history(self) -> list[ManagedState]:
        _bound(self)
        return self._manager.managed_history(
            self.reference.publisher_id, self.reference.skill_id
        )

    @sdk_operation
    def compare(
        self, before_revision: str, after_revision: str | None = None
    ) -> ComparisonResult:
        _bound(self)
        return self._manager.compare_managed(
            self.reference.publisher_id,
            self.reference.skill_id,
            before_revision,
            after_revision,
        )

    @sdk_operation
    def rollback(self, revision: str, *, expected_revision: str) -> ManagedStatus:
        _bound(self)
        return self._manager.rollback_managed(
            self.reference.publisher_id,
            self.reference.skill_id,
            revision,
            expected_revision=expected_revision,
        )

    @sdk_operation
    def release(self, version: str, *, revision: str | None = None) -> ReleaseRecord:
        _bound(self)
        return self._manager.release_managed(
            self.reference.publisher_id,
            self.reference.skill_id,
            version,
            revision=revision,
        )

    @sdk_operation
    def export(
        self, destination: str | PathLike, *, revision: str | None = None
    ) -> ExportResult:
        return self.resolve(revision=revision).export(destination)

    @sdk_operation
    def set_tracking(self, enabled: bool, *, expected_revision: str) -> ManagedStatus:
        _bound(self)
        return self._manager.set_managed_tracking(
            self.reference.publisher_id,
            self.reference.skill_id,
            enabled,
            expected_revision=expected_revision,
        )

    @sdk_operation
    def check_updates(
        self, *, expected_revision: str | None = None, force: bool = False
    ) -> ManagedStatus:
        _bound(self)
        return self._manager.check_updates(
            self.reference.publisher_id,
            self.reference.skill_id,
            expected_revision=expected_revision,
            force=force,
        )

    @sdk_operation
    def source_status(self) -> dict[str, Any]:
        _bound(self)
        return self._manager.source_status(
            self.reference.publisher_id, self.reference.skill_id
        )

    @sdk_operation
    def source_settings(
        self,
        *,
        expected_revision: str,
        pinned: bool | None = None,
        interval_hours: int | None = None,
        label: str | None = None,
    ) -> ManagedStatus:
        _bound(self)
        return self._manager.source_settings(
            self.reference.publisher_id,
            self.reference.skill_id,
            expected_revision=expected_revision,
            pinned=pinned,
            interval_hours=interval_hours,
            label=label,
        )

    @sdk_operation
    def switch_source(
        self, *, expected_revision: str, ref: str, track: bool | None = None
    ) -> ManagedStatus:
        _bound(self)
        return self._manager.switch_source(
            self.reference.publisher_id,
            self.reference.skill_id,
            expected_revision=expected_revision,
            ref=ref,
            track=track,
        )
