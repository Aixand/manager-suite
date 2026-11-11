"""Version 1 public SDK identities and JSON result contracts."""

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal, NotRequired, TypedDict

from ..errors import require
from ..prompts.definitions import name

SDK_CONTRACT_VERSION = 1
OutputFormat = Literal["text", "messages"]
ReconciliationSide = Literal["managed", "publisher"]


@dataclass(frozen=True)
class PackageReference:
    """Serializable identity bound to one store, independent of publisher files."""

    store: str
    publisher_id: str
    package_id: str = "default"
    schema: ClassVar[str] = "prompt-manager/package-ref-1"

    def __post_init__(self):
        require(
            isinstance(self.store, str)
            and "\0" not in self.store
            and Path(self.store).is_absolute()
            and ".." not in Path(self.store).parts
            and str(Path(self.store)) == self.store,
            "Package store must be an absolute normalized path",
        )
        name(self.publisher_id, "publisher_id")
        name(self.package_id, "package_id")

    def to_dict(self) -> dict[str, str]:
        return {"schema": self.schema, **asdict(self)}

    @classmethod
    def from_dict(cls, value: dict[str, str]) -> "PackageReference":
        require(
            isinstance(value, dict)
            and set(value) == {"schema", "store", "publisher_id", "package_id"}
            and value["schema"] == cls.schema,
            "Invalid package reference schema or fields",
        )
        return cls(value["store"], value["publisher_id"], value["package_id"])


class Diagnostic(TypedDict):
    code: str
    message: str


class PublisherSnapshot(TypedDict):
    digest: str
    revision: int | None
    version: str | None


class ContentMetadata(TypedDict):
    digest: str
    name: str
    description: str


class ManagedState(TypedDict):
    schema: str
    publisher_id: str
    id: str
    revision: str
    active_digest: str
    baseline: PublisherSnapshot
    upstream: PublisherSnapshot
    tracking: bool
    sync_error: Diagnostic | None
    sequence: int
    previous: str | None
    label: NotRequired[str]
    archived: NotRequired[bool]
    tags: NotRequired[list[str]]
    content_metadata: NotRequired[ContentMetadata]


class ManagedStatus(ManagedState):
    customized: bool
    baseline_known: bool
    reconciliation_required: bool
    update_status: str
    outcome: NotRequired[str]


class ManagedResolution(TypedDict):
    schema: str
    snapshot: dict[str, Any]
    resolution: ManagedStatus
    digest: str


class _RenderFields(TypedDict):
    schema: str
    renderer: str
    prompt: str
    release: "SnapshotIdentity"
    format: OutputFormat
    output: str | list[dict[str, str]]
    input_digest: str
    output_digest: str
    sections: list[dict[str, Any]]
    dependencies: dict[str, "DependencyIdentity"]


class RenderResult(_RenderFields):
    resolution: ManagedStatus


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
    schema: str
    valid: bool
    revision: str
    digest: str
    prompts: list[str]
    examples_passed: list[str]


class ComparisonResult(TypedDict):
    changed: bool
    before: dict[str, Any]
    after: dict[str, Any]
    definition_diff: str
    render_diff: str | None


class ExportResult(TypedDict):
    schema: str
    path: str
    revision: str
    digest: str


class ReleaseIdentity(TypedDict):
    name: str
    version: str
    digest: str


@dataclass(frozen=True)
class RegistrationResult:
    """Effective rendered content and registration status from one captured state."""

    content: str | list[dict[str, str]]
    status: ManagedStatus
    schema: ClassVar[str] = "prompt-manager/registration-result-1"

    def to_dict(self) -> dict[str, Any]:
        return {"schema": self.schema, **asdict(self)}


class ManagedTarget(TypedDict):
    kind: Literal["managed"]
    publisher_id: str
    package_id: str
    prompt: str
    revision: NotRequired[str]


class ReleaseTarget(TypedDict):
    kind: Literal["release"]
    reference: str


class DraftTarget(TypedDict):
    kind: Literal["draft"]
    source_id: str
    pack_path: str
    prompt: str


PromptTarget = ManagedTarget | ReleaseTarget | DraftTarget
FallbackPolicy = Literal["none", "packaged"]


class PackagedFallback(TypedDict):
    pack_path: str
    prompt: str


class PackagedTarget(PackagedFallback):
    kind: Literal["packaged"]


class SourceIdentity(TypedDict):
    schema: str
    id: str
    label: str
    paths: list[str]


class SourceRegistration(SourceIdentity):
    """Unguarded source upserts may omit normalization metadata and revision."""

    owner: NotRequired[Any]
    writable: NotRequired[bool]
    revision: NotRequired[str]


class SourceDescriptor(SourceIdentity):
    owner: Any
    writable: bool
    revision: str


class RegistrationIssue(TypedDict):
    id: str
    label: str
    error: str
    diagnostic: Diagnostic


class ApplicationDescriptor(TypedDict):
    schema: str
    id: str
    label: str
    prompts: dict[str, PromptTarget]
    fallback_policy: FallbackPolicy
    revision: str


class SnapshotIdentity(TypedDict):
    name: str
    version: str | None
    digest: str


class DependencyIdentity(TypedDict):
    name: str
    digest: str


class ApplicationResolution(TypedDict):
    schema: str
    application_id: str
    application_revision: str | None
    logical_prompt: str
    target: PromptTarget | PackagedTarget
    store: str
    store_id: str
    source_id: str | None
    catalog_entry_id: str | None
    path: str | None
    reference: str | None
    editable: bool
    release: SnapshotIdentity
    dependencies: dict[str, DependencyIdentity]
    used_fallback: bool
    managed: NotRequired[ManagedStatus]


class ApplicationRenderResult(_RenderFields):
    resolution: ApplicationResolution


class StoreFinding(TypedDict):
    severity: Literal["error", "warning"]
    category: Literal["corruption", "access", "availability", "unreferenced", "concurrent_change"]
    code: str
    path: str
    message: str
    diagnostic: NotRequired[Diagnostic]


class StoreCheckResult(TypedDict):
    schema: str
    store: str
    exists: bool
    ok: bool
    complete: bool
    stable: bool
    references_complete: bool
    counts: dict[str, int]
    findings: list[StoreFinding]


class ExternalSource(TypedDict):
    id: str
    paths: list[str]


class StoreBackupResult(TypedDict):
    schema: str
    path: str
    source_store: str
    digest: str
    validated: bool
    file_count: int
    content_bytes: int
    external_drafts_included: Literal[False]
    external_sources: list[ExternalSource]
    excluded: list[str]
    warnings: list[StoreFinding]


class StoreRestoreResult(StoreBackupResult):
    store: str


class ManagedSearchReference(TypedDict):
    publisher_id: str
    package_id: str
    revision: str
    active_digest: str


class ManagedSearchEntry(ManagedSearchReference):
    label: str
    pack_name: str | None
    description: str | None
    tags: list[str]
    archived: bool
    customized: bool
    reconciliation_required: bool
    tracking: bool
    metadata_complete: bool


class ManagedSearchIssue(TypedDict):
    publisher_id: str
    package_id: str
    error: Diagnostic


class ManagedSearchResult(TypedDict):
    schema: str
    store: str
    query: str
    items: list[ManagedSearchEntry]
    issues: list[ManagedSearchIssue]
    metadata_missing: list[ManagedSearchReference]
    metadata_complete: bool
    complete: bool
