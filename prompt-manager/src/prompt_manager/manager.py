"""Stable public API coordinating independently owned prompt operations."""

import builtins
import os
from collections.abc import Sequence
from os import PathLike
from pathlib import Path
from typing import Any

from .applications import resolution
from .codec import clone
from .errors import PromptError, operation, require
from .interfaces.contracts import (
    ApplicationDescriptor,
    ApplicationRenderResult,
    ApplicationResolution,
    FallbackPolicy,
    ManagedSearchResult,
    ManagedStatus,
    OutputFormat,
    PackagedFallback,
    PackageReference,
    PromptTarget,
    RegistrationIssue,
    RegistrationResult,
    SourceDescriptor,
    SourceRegistration,
    StoreBackupResult,
    StoreCheckResult,
    StoreRestoreResult,
)
from .interfaces.sdk import Package
from .library import (
    authoring,
    backup,
    catalog,
    comparison,
    discovery,
    integrity,
    releases,
    snapshots,
)
from .managed import authoring as managed_authoring
from .managed import inspection, organization, registration, search, workspace
from .managed.state import engine
from .packages import PackageLifecycle
from .prompts.rendering import render as render_snapshot
from .sources.registry import Registry
from .storage.records import UNSET


class Manager:
    resource_kind = "prompts"

    def __init__(self, home=None, *, packs=None):
        self.store = releases.Store(home)
        if packs is None:
            configured = os.environ.get("PROMPT_MANAGER_PACKS")
            packs = configured.split(os.pathsep) if configured else [Path.cwd() / "packs"]
        self.packs = [Path(path).resolve() for path in packs]

    def package(self, publisher_id: str, package_id: str = "default") -> Package:
        """Bind an identity without reading or registering package content."""
        return Package(self, PackageReference(str(self.store.home), publisher_id, package_id))

    def lifecycle(self) -> PackageLifecycle:
        """Use the shared package lifecycle SDK with this store's prompt format."""
        return PackageLifecycle(registration.Backend(self.store))

    def open_package(self, reference: PackageReference | dict[str, str]) -> Package:
        """Recover a serialized reference, rejecting a different store."""
        if isinstance(reference, dict):
            reference = PackageReference.from_dict(reference)
        require(isinstance(reference, PackageReference), "Expected a package reference")
        return Package(self, reference)

    @operation
    def application_descriptor(self, application_id: str) -> ApplicationDescriptor:
        """Read a logical selection descriptor without registration side effects."""
        return self._registry().get("applications", application_id)

    def _registry(self):
        return Registry(self.store.home)

    @operation
    def source_descriptor(self, source_id: str) -> SourceDescriptor:
        """Read a normalized source descriptor, including its revision."""
        return self._registry().get("sources", source_id)

    @operation
    def register_source(
        self,
        identifier: str,
        paths: Sequence[str | PathLike[str]],
        *,
        label: str | None = None,
        owner: Any = None,
        writable: bool = True,
        expected_revision: object = UNSET,
    ) -> SourceRegistration | SourceDescriptor:
        return self._registry().source(
            identifier,
            paths,
            label=label,
            owner=owner,
            writable=writable,
            expected_revision=expected_revision,
        )

    @operation
    def sources(self) -> builtins.list[SourceDescriptor | RegistrationIssue]:
        return self._registry().list("sources")

    @operation
    def unregister_source(self, identifier: str, *, expected_revision: str) -> SourceDescriptor:
        return self._registry().remove_guarded(
            "sources", identifier, expected_revision=expected_revision
        )

    @operation
    def register_application(
        self,
        identifier: str,
        *,
        prompts: dict[str, PromptTarget],
        label: str | None = None,
        fallback_policy: FallbackPolicy = "none",
        expected_revision: str | None = None,
    ) -> ApplicationDescriptor:
        return self._registry().application(
            identifier,
            prompts=prompts,
            label=label,
            fallback_policy=fallback_policy,
            expected_revision=expected_revision,
        )

    def application(
        self,
        identifier,
        *,
        bundled_pack=None,
        package_id="default",
        publisher_revision=None,
        version=None,
    ):
        if bundled_pack is not None:
            from .interfaces.legacy_application import Application

            return Application(
                self,
                identifier,
                bundled_pack,
                package_id=package_id,
                publisher_revision=publisher_revision,
                version=version,
            )
        return self.application_descriptor(identifier)

    def register_prompt(
        self,
        publisher_id: str,
        source: str | PathLike[str],
        *,
        package_id: str = "default",
        prompt_id: str | None = None,
        inputs: dict | None = None,
        format: OutputFormat = "text",
        publisher_revision: int | None = None,
        version: str | None = None,
    ) -> RegistrationResult:
        """Return usable effective content with registration/update diagnostics."""
        require(format in {"text", "messages"}, "Format must be text or messages")
        values = clone({} if inputs is None else inputs)
        try:
            captured = registration.register(
                self._managed(), publisher_id, package_id, source, publisher_revision, version
            )
            snapshot, status = captured["snapshot"], captured["status"]
            selected = snapshots.select(snapshot, prompt_id)
            identity = {
                "name": snapshot["pack"]["name"],
                "version": None,
                "digest": status["active_digest"],
            }
            rendered = render_snapshot(snapshot, selected, values, identity, format)
            return RegistrationResult(content=rendered["output"], status=status)
        except OSError as error:
            raise PromptError("io", str(error)) from error

    def ensure_registered(
        self, publisher, path, *, package_id="default", publisher_revision=None, version=None
    ):
        return self._managed().ensure(publisher, package_id, path, publisher_revision, version)

    def resolve_managed(self, publisher, identifier="default", *, revision=None):
        return inspection.resolve(self._managed(), publisher, identifier, revision=revision)

    def render_managed(
        self, publisher, identifier, prompt, inputs=None, *, format="text", revision=None
    ):
        return inspection.render_managed(
            self._managed(), publisher, identifier, prompt, inputs, format=format, revision=revision
        )

    def release_managed(self, publisher, identifier, version, *, revision=None):
        owned = self.resolve_managed(publisher, identifier, revision=revision)
        return self.store.publish(owned["snapshot"], version)

    @operation
    def applications(self) -> builtins.list[ApplicationDescriptor | RegistrationIssue]:
        return self._registry().list("applications")

    @operation
    def set_application_prompt(
        self,
        identifier: str,
        logical_prompt: str,
        target: PromptTarget | None,
        *,
        expected_revision: str,
    ) -> ApplicationDescriptor:
        return self._registry().set_prompt(
            identifier,
            logical_prompt,
            target,
            expected_revision=expected_revision,
        )

    @operation
    def unregister_application(
        self,
        identifier: str,
        *,
        expected_revision: str,
    ) -> ApplicationDescriptor:
        return self._registry().remove_guarded(
            "applications",
            identifier,
            expected_revision=expected_revision,
        )

    @operation
    def resolve_application(
        self,
        identifier: str,
        logical_prompt: str,
        *,
        fallback: PackagedFallback | None = None,
    ) -> ApplicationResolution:
        return resolution.capture(
            self.store,
            self.packs,
            self._registry(),
            self._managed(),
            identifier,
            logical_prompt,
            fallback,
        )[0]

    @operation
    def render_application(
        self,
        identifier: str,
        logical_prompt: str,
        inputs: dict[str, Any] | None = None,
        *,
        format: OutputFormat = "text",
        fallback: PackagedFallback | None = None,
    ) -> ApplicationRenderResult:
        return resolution.render_application(
            self.store,
            self.packs,
            self._registry(),
            self._managed(),
            identifier,
            logical_prompt,
            inputs,
            format=format,
            fallback=fallback,
        )

    @operation
    def unregister(self, kind: str, identifier: str) -> None:
        Registry(self.store.home).remove(kind, identifier)

    @operation
    def backup_store(self, destination: str | PathLike[str]) -> StoreBackupResult:
        """Capture and validate all supported store records without external draft files."""
        return backup.backup_store(self.store.home, destination)

    @operation
    def restore_store(self, source: str | PathLike[str]) -> StoreRestoreResult:
        """Restore a validated archive into this Manager's unused home path."""
        return backup.restore_store(self.store.home, source)

    @operation
    def check_store(self, *, include_external: bool = True) -> StoreCheckResult:
        """Inspect integrity and availability without writing or repairing storage."""
        return integrity.check_store(
            self.store, self._registry(), self._managed(), include_external=include_external
        )

    def catalog(self, *, include_archived: bool = False):
        return catalog.catalog(
            self.store,
            self.packs,
            self._registry(),
            self._managed(),
            include_archived=include_archived,
        )

    def catalog_source(self, identifier):
        return discovery.source(self.store, self.packs, self._registry(), identifier)

    def show_catalog(self, identifier, *, prompt=None):
        return authoring.show_catalog(
            self.store, self.packs, self._registry(), identifier, prompt=prompt
        )

    def save_catalog_file(self, identifier, file, text, *, expected_digest):
        return authoring.save_catalog_file(
            self.store,
            self.packs,
            self._registry(),
            identifier,
            file,
            text,
            expected_digest=expected_digest,
        )

    def release_catalog(self, identifier, version, *, expected_digest):
        return releases.release_catalog(
            self.store,
            self.packs,
            self._registry(),
            identifier,
            version,
            expected_digest=expected_digest,
        )

    def list(self):
        return discovery.list_packs(self.store, self.packs)

    def show(self, source, *, prompt=None):
        return authoring.show(self.store, self.packs, source, prompt=prompt)

    def validate(self, source):
        return authoring.validate(self.store, self.packs, source)

    def render(self, source, inputs=None, *, prompt=None, format="text"):
        return snapshots.render_source(
            self.store, self.packs, source, inputs, prompt=prompt, format=format
        )

    def release(self, source, version):
        return releases.release(self.store, self.packs, source, version)

    def diff(self, before, after, *, inputs=None, prompt=None, format="text"):
        return comparison.compare(
            self.store, self.packs, before, after, inputs=inputs, prompt=prompt, format=format
        )

    def _managed(self):
        return engine(self.store)

    def managed_status(self, publisher, identifier, *, revision=None):
        return self._managed().status(publisher, identifier, revision)

    def managed_catalog(self, *, include_archived: bool = False):
        return self._managed().catalog(include_archived=include_archived)

    def managed_summaries(self, *, include_archived: bool = False):
        return self._managed().catalog(verify=False, include_archived=include_archived)

    @operation
    def search_managed(
        self,
        query: str = "",
        *,
        publisher_id: str | None = None,
        tags: Sequence[str] = (),
        archived: bool | None = False,
        customized: bool | None = None,
        reconciliation_required: bool | None = None,
    ) -> ManagedSearchResult:
        return search.search(
            self._managed(),
            query,
            publisher_id=publisher_id,
            tags=tags,
            archived=archived,
            customized=customized,
            reconciliation_required=reconciliation_required,
        )

    @operation
    def set_managed_tags(
        self,
        publisher_id: str,
        package_id: str,
        tags: Sequence[str],
        *,
        expected_revision: str,
    ) -> ManagedStatus:
        return organization.set_tags(
            self._managed(), publisher_id, package_id, tags, expected_revision
        )

    @operation
    def refresh_managed_metadata(
        self,
        publisher_id: str,
        package_id: str,
        *,
        expected_revision: str,
    ) -> ManagedStatus:
        return organization.refresh_metadata(
            self._managed(), publisher_id, package_id, expected_revision
        )

    @operation
    def rename_managed(
        self,
        publisher_id: str,
        package_id: str,
        label: str | None,
        *,
        expected_revision: str,
    ) -> ManagedStatus:
        return organization.rename(
            self._managed(), publisher_id, package_id, label, expected_revision
        )

    @operation
    def set_managed_archived(
        self,
        publisher_id: str,
        package_id: str,
        enabled: bool,
        *,
        expected_revision: str,
    ) -> ManagedStatus:
        return organization.set_archived(
            self._managed(), publisher_id, package_id, enabled, expected_revision
        )

    @operation
    def duplicate_managed(
        self,
        publisher_id: str,
        package_id: str,
        target_id: str,
        *,
        revision: str | None = None,
        label: str | None = None,
    ) -> ManagedStatus:
        return organization.duplicate(
            self._managed(), publisher_id, package_id, target_id, revision=revision, label=label
        )

    def report_managed_sync_error(self, publisher, identifier, message, *, code="io"):
        return self._managed().report_error(publisher, identifier, message, code)

    def adopt_legacy(
        self,
        publisher,
        identifier,
        legacy_source,
        publisher_source,
        *,
        publisher_revision=None,
        version=None,
        expected_revision=None,
    ):
        return self._managed().adopt(
            publisher,
            identifier,
            legacy_source,
            publisher_source,
            publisher_revision=publisher_revision,
            version=version,
            expected_revision=expected_revision,
        )

    def managed_view(self, publisher, identifier, **options):
        return inspection.view(self._managed(), publisher, identifier, **options)

    def managed_history(self, publisher, identifier):
        return self._managed().history(publisher, identifier)

    def save_managed(self, publisher, identifier, files, *, expected_revision):
        return self._managed().edit(publisher, identifier, files, expected_revision)

    def preview_reconciliation(self, publisher, identifier, *, resolutions=None, take=None):
        return self._managed().preview(publisher, identifier, resolutions, take)

    def reconcile(self, publisher, identifier, *, expected_revision, resolutions=None, take=None):
        return self._managed().reconcile(
            publisher, identifier, expected_revision, resolutions, take
        )

    def rollback_managed(self, publisher, identifier, revision, *, expected_revision):
        return self._managed().rollback(publisher, identifier, revision, expected_revision)

    def set_managed_tracking(self, publisher, identifier, enabled, *, expected_revision):
        return self._managed().set_tracking(publisher, identifier, enabled, expected_revision)

    def create_managed(self, name: str, *, content: str = "", role: str = "user") -> ManagedStatus:
        return managed_authoring.create(self._managed(), name, content=content, role=role)

    def import_local(
        self, path: str | PathLike[str], *, package_id: str | None = None
    ) -> ManagedStatus:
        return managed_authoring.import_local(self._managed(), path, package_id=package_id)

    def validate_managed(self, publisher, identifier, files=None, *, revision=None):
        return workspace.validate(self._managed(), publisher, identifier, files, revision=revision)

    def compare_managed(
        self,
        publisher,
        identifier,
        before_revision,
        after_revision=None,
        *,
        inputs=None,
        prompt=None,
        format="text",
    ):
        return workspace.compare(
            self._managed(),
            publisher,
            identifier,
            before_revision,
            after_revision,
            inputs=inputs,
            prompt=prompt,
            format=format,
        )

    def export_managed(self, publisher, identifier, destination, *, revision=None):
        return workspace.export(
            self._managed(), publisher, identifier, destination, revision=revision
        )
