"""Version 1 public SDK identities, binary inputs, and native JSON contracts."""

import base64
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal, NotRequired, TypedDict

from .codec import source_id
from .errors import require

SDK_CONTRACT_VERSION = 1
ReconciliationSide = Literal["managed", "publisher"]


@dataclass(frozen=True)
class SkillReference:
    store: str
    publisher_id: str
    skill_id: str
    schema: ClassVar[str] = "skill-manager/skill-ref-1"

    def __post_init__(self):
        require(
            isinstance(self.store, str)
            and "\0" not in self.store
            and Path(self.store).is_absolute()
            and ".." not in Path(self.store).parts
            and str(Path(self.store)) == self.store,
            "Skill store must be an absolute normalized path",
        )
        source_id(self.publisher_id)
        source_id(self.skill_id)

    def to_dict(self) -> dict[str, str]:
        return {
            "schema": self.schema,
            "store": self.store,
            "publisher_id": self.publisher_id,
            "skill_id": self.skill_id,
        }

    @classmethod
    def from_dict(cls, value: dict[str, str]) -> "SkillReference":
        require(
            isinstance(value, dict)
            and set(value) == {"schema", "store", "publisher_id", "skill_id"}
            and value["schema"] == cls.schema,
            "Invalid skill reference",
        )
        return cls(value["store"], value["publisher_id"], value["skill_id"])


@dataclass(frozen=True)
class FileContent:
    content: bytes
    executable: bool = False

    def __post_init__(self):
        require(
            isinstance(self.content, bytes) and type(self.executable) is bool,
            "FileContent needs original bytes and a boolean executable flag",
        )

    def to_dict(self) -> dict[str, Any]:
        """Portable JSON representation; SDK saves use the original bytes."""
        return {
            "content_b64": base64.b64encode(self.content).decode("ascii"),
            "executable": self.executable,
        }


class FileSpec(TypedDict):
    content: bytes
    executable: bool


FileChanges = Mapping[str, FileContent | FileSpec | None]


def file_inputs(files: FileChanges) -> dict[str, FileSpec | None]:
    require(
        isinstance(files, Mapping),
        "Files must map paths to FileContent, byte specifications, or null",
    )
    result = {}
    for path, value in files.items():
        require(isinstance(path, str), "File paths must be text")
        if isinstance(value, FileContent):
            result[path] = {"content": value.content, "executable": value.executable}
        else:
            require(
                value is None
                or isinstance(value, dict)
                and set(value) == {"content", "executable"}
                and isinstance(value["content"], bytes)
                and type(value["executable"]) is bool,
                "Each file needs FileContent, bytes content/bool executable, or null",
            )
            result[path] = None if value is None else dict(value)
    return result


class Diagnostic(TypedDict):
    code: str
    message: str
    uncertain: NotRequired[bool]
    operation_id: NotRequired[str | None]
    recovery: NotRequired[Any]


class PublisherSnapshot(TypedDict):
    digest: str
    revision: int | None
    version: str | None


class ManagedState(TypedDict):
    schema: str
    publisher_id: str
    id: str
    baseline: PublisherSnapshot
    upstream: PublisherSnapshot
    active_digest: str
    revision: str
    sequence: int
    previous: str | None
    tracking: bool
    sync_error: Diagnostic | None
    source: NotRequired[dict[str, Any]]
    migration: NotRequired[dict[str, Any] | None]
    source_review: NotRequired[bool]
    hold: NotRequired[bool]
    label: NotRequired[str]


class ManagedStatus(ManagedState):
    customized: bool
    baseline_known: bool
    reconciliation_required: bool
    update_status: str
    outcome: NotRequired[str]


class ManifestFile(TypedDict):
    path: str
    size: int
    digest: str
    executable: bool


class SkillManifest(TypedDict):
    schema: str
    name: str
    metadata: dict[str, Any]
    directories: list[str]
    files: list[ManifestFile]


class ManagedResolution(TypedDict):
    schema: str
    store: str
    store_id: str
    source_id: str
    name: str
    reference: str
    digest: str
    manifest: SkillManifest
    resolution: ManagedStatus
    object: str
    blobs: dict[str, str]


class ReconciliationPreview(TypedDict):
    revision: str
    baseline_known: bool
    candidate: dict[str, Any]
    conflicts: list[dict[str, Any]]
    valid: bool
    validation_error: Diagnostic | None
    managed_changes: list[dict[str, Any]]
    publisher_changes: list[dict[str, Any]]


class ValidationResult(TypedDict):
    valid: bool
    revision: str
    digest: str


class ComparisonResult(TypedDict):
    before: ManagedStatus
    after: ManagedStatus
    changes: list[dict[str, Any]]


class ExportResult(TypedDict):
    schema: str
    reference: str
    digest: str
    destination: str
    status: str


class ReleaseRecord(TypedDict):
    schema: str
    reference: str
    source_id: str
    source_revision: str
    source_path: str
    name: str
    version: str
    digest: str
    provenance: dict[str, Any]


class ProjectPlan(TypedDict):
    schema: str
    action: str
    project: str
    store_id: str
    intent: str
    before: str | None
    before_state: dict[str, Any] | None
    manifest_before: dict[str, Any] | None
    owner_before: dict[str, Any] | None
    after: dict[str, Any]
    changes: list[dict[str, Any]]
    token: str


class CollectionCandidate(TypedDict):
    path: str
    name: str
    digest: str | None
    valid: bool
    error: Diagnostic | str | None
    description: str


class CollectionDiscovery(TypedDict):
    schema: str
    root: str
    candidates: list[CollectionCandidate]
    diagnostics: list[dict[str, str]]
    revision: str


class CollectionImportItem(TypedDict):
    path: str
    status: ManagedStatus


class CollectionImport(TypedDict):
    schema: str
    root: str
    items: list[CollectionImportItem]


class ProjectOperation(TypedDict):
    schema: str
    id: str
    plan: ProjectPlan
    status: str
    error: str | None
    revision: NotRequired[str]
    staged_identities: NotRequired[dict[str, list[int]]]
    steps: NotRequired[dict[str, str]]


class ProjectConnections(TypedDict):
    schema: str
    project: str
    revision: str | None
    skills_dir: str | None
    manifest_modified: bool
    owner_mismatch: bool
    pending_operation: str | None
    connections: list[dict[str, Any]]
    operations: list[ProjectOperation]
