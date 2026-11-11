"""Explicit synchronous package handle over public Manager operations."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from os import PathLike
from typing import Any

from ..errors import operation, require
from .contracts import (
    ComparisonResult,
    ExportResult,
    ManagedResolution,
    ManagedState,
    ManagedStatus,
    OutputFormat,
    PackageReference,
    ReconciliationPreview,
    ReconciliationSide,
    RegistrationResult,
    ReleaseIdentity,
    RenderResult,
    ValidationResult,
)


@dataclass(frozen=True)
class Package:
    """Explicit synchronous operations on an owned package in one exact store."""

    _manager: Any = field(repr=False, compare=False)
    reference: PackageReference

    def __post_init__(self):
        require(isinstance(self.reference, PackageReference), "Expected a package reference")
        require(
            str(self._manager.store.home) == self.reference.store,
            "Package reference belongs to another store",
            "conflict",
        )

    @operation
    def register(
        self,
        source: str | PathLike[str],
        *,
        prompt_id: str | None = None,
        inputs: dict[str, Any] | None = None,
        format: OutputFormat = "text",
        publisher_revision: int | None = None,
        version: str | None = None,
    ) -> RegistrationResult:
        """Register defaults and return effective content, keeping usable user edits."""
        return self._manager.register_prompt(
            self.reference.publisher_id,
            source,
            package_id=self.reference.package_id,
            prompt_id=prompt_id,
            inputs=inputs,
            format=format,
            publisher_revision=publisher_revision,
            version=version,
        )

    @operation
    def refresh(
        self,
        source: str | PathLike[str],
        *,
        publisher_revision: int | None = None,
        version: str | None = None,
    ) -> ManagedStatus:
        return self._manager.ensure_registered(
            self.reference.publisher_id,
            source,
            package_id=self.reference.package_id,
            publisher_revision=publisher_revision,
            version=version,
        )

    @operation
    def status(self, *, revision: str | None = None) -> ManagedStatus:
        return self._manager.managed_status(
            self.reference.publisher_id, self.reference.package_id, revision=revision
        )

    @operation
    def resolve(self, *, revision: str | None = None) -> ManagedResolution:
        return self._manager.resolve_managed(
            self.reference.publisher_id, self.reference.package_id, revision=revision
        )

    @operation
    def render(
        self,
        prompt_id: str,
        inputs: dict[str, Any] | None = None,
        *,
        format: OutputFormat = "text",
        revision: str | None = None,
    ) -> RenderResult:
        return self._manager.render_managed(
            self.reference.publisher_id,
            self.reference.package_id,
            prompt_id,
            inputs,
            format=format,
            revision=revision,
        )

    @operation
    def set_tags(self, tags: Sequence[str], *, expected_revision: str) -> ManagedStatus:
        """Replace all tags using normalized, guarded metadata."""
        return self._manager.set_managed_tags(
            self.reference.publisher_id,
            self.reference.package_id,
            tags,
            expected_revision=expected_revision,
        )

    @operation
    def refresh_metadata(self, *, expected_revision: str) -> ManagedStatus:
        """Populate accepted name/description metadata without refreshing the publisher."""
        return self._manager.refresh_managed_metadata(
            self.reference.publisher_id,
            self.reference.package_id,
            expected_revision=expected_revision,
        )

    @operation
    def rename(self, label: str | None, *, expected_revision: str) -> ManagedStatus:
        """Change the display name; null clears the override without changing IDs."""
        return self._manager.rename_managed(
            self.reference.publisher_id,
            self.reference.package_id,
            label,
            expected_revision=expected_revision,
        )

    @operation
    def archive(self, *, expected_revision: str) -> ManagedStatus:
        return self._manager.set_managed_archived(
            self.reference.publisher_id,
            self.reference.package_id,
            True,
            expected_revision=expected_revision,
        )

    @operation
    def unarchive(self, *, expected_revision: str) -> ManagedStatus:
        return self._manager.set_managed_archived(
            self.reference.publisher_id,
            self.reference.package_id,
            False,
            expected_revision=expected_revision,
        )

    @operation
    def duplicate(
        self,
        target_id: str,
        *,
        revision: str | None = None,
        label: str | None = None,
    ) -> ManagedStatus:
        """Create a local, nontracking copy with its own pack name and history."""
        return self._manager.duplicate_managed(
            self.reference.publisher_id,
            self.reference.package_id,
            target_id,
            revision=revision,
            label=label,
        )

    @operation
    def save(self, files: dict[str, str | None], *, expected_revision: str) -> ManagedStatus:
        return self._manager.save_managed(
            self.reference.publisher_id,
            self.reference.package_id,
            files,
            expected_revision=expected_revision,
        )

    @operation
    def preview_reconciliation(
        self, *, resolutions: dict[str, Any] | None = None, take: ReconciliationSide | None = None
    ) -> ReconciliationPreview:
        return self._manager.preview_reconciliation(
            self.reference.publisher_id,
            self.reference.package_id,
            resolutions=resolutions,
            take=take,
        )

    @operation
    def reconcile(
        self,
        *,
        expected_revision: str,
        resolutions: dict[str, Any] | None = None,
        take: ReconciliationSide | None = None,
    ) -> ManagedStatus:
        return self._manager.reconcile(
            self.reference.publisher_id,
            self.reference.package_id,
            expected_revision=expected_revision,
            resolutions=resolutions,
            take=take,
        )

    @operation
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
        return self._manager.managed_view(
            self.reference.publisher_id,
            self.reference.package_id,
            review=review,
            path=path,
            expected_revision=expected_revision,
            resolutions=resolutions,
            take=take,
            offset=offset,
            limit=limit,
            conflict_offset=conflict_offset,
        )

    @operation
    def history(self) -> list[ManagedState]:
        return self._manager.managed_history(self.reference.publisher_id, self.reference.package_id)

    @operation
    def restore(self, revision: str, *, expected_revision: str) -> ManagedStatus:
        return self._manager.rollback_managed(
            self.reference.publisher_id,
            self.reference.package_id,
            revision,
            expected_revision=expected_revision,
        )

    @operation
    def set_tracking(self, enabled: bool, *, expected_revision: str) -> ManagedStatus:
        return self._manager.set_managed_tracking(
            self.reference.publisher_id,
            self.reference.package_id,
            enabled,
            expected_revision=expected_revision,
        )

    @operation
    def release(self, version: str, *, revision: str | None = None) -> ReleaseIdentity:
        return self._manager.release_managed(
            self.reference.publisher_id, self.reference.package_id, version, revision=revision
        )

    @operation
    def validate(
        self, files: dict[str, str | None] | None = None, *, revision: str | None = None
    ) -> ValidationResult:
        return self._manager.validate_managed(
            self.reference.publisher_id, self.reference.package_id, files, revision=revision
        )

    @operation
    def compare(
        self,
        before_revision: str,
        after_revision: str | None = None,
        *,
        prompt_id: str | None = None,
        inputs: dict[str, Any] | None = None,
        format: OutputFormat = "text",
    ) -> ComparisonResult:
        return self._manager.compare_managed(
            self.reference.publisher_id,
            self.reference.package_id,
            before_revision,
            after_revision,
            prompt=prompt_id,
            inputs=inputs,
            format=format,
        )

    @operation
    def export(
        self, destination: str | PathLike[str], *, revision: str | None = None
    ) -> ExportResult:
        return self._manager.export_managed(
            self.reference.publisher_id, self.reference.package_id, destination, revision=revision
        )
