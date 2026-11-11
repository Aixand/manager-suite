"""Skill-owned creation, import, source policy, and acquisition records."""

from __future__ import annotations

import base64
import tempfile
import time
from copy import deepcopy
from pathlib import Path

from ._source_records import (
    SourceError,
    atomic,
    canonical,
    digest,
    identifier,
    read,
    require,
    slug,
    validate_source,
    validate_tree,
)
from .github_source import GitHub
from .source_discovery import discover, package_tree


class Sources:
    def __init__(self, manager):
        self.manager = manager
        self.engine = manager._managed()
        self.backend = self.engine.backend
        self.home = self.backend.home
        self.kind = manager.resource_kind

    @property
    def github(self):
        return getattr(self.manager, "source_client", None) or GitHub()

    def object(self, tree):
        validate_tree(tree)
        identity = digest(tree)
        path = self.home / "source-objects" / (identity[7:] + ".json")
        with self.backend.lock():
            previous = read(path)
            require(
                previous is None or previous == tree,
                "Source object identity collision",
                "integrity",
            )
            if previous is None:
                atomic(path, tree)
        return identity

    def tree(self, identity):
        from ._source_records import DIGEST

        require(
            isinstance(identity, str) and DIGEST.fullmatch(identity),
            "Invalid source object identity",
        )
        result = read(self.home / "source-objects" / (identity[7:] + ".json"))
        require(
            result is not None and digest(result) == identity,
            "Source snapshot unavailable or damaged",
            "integrity",
        )
        return validate_tree(result)

    def discover(self, url, *, ref=None, path=None, track=None):
        source = self.github.resolve(url, ref=ref, path=path, track=track)
        tree, _ = self.github.snapshot(source)
        identity = self.object(tree)
        candidates = discover(self.kind, tree, source)
        require(
            candidates,
            "No supported resources found at the selected location",
            "not_found",
        )
        record = {
            "schema": "resource-sources/discovery-1",
            "kind": self.kind,
            "source": {**source, "source_digest": identity},
            "candidates": candidates,
        }
        require(
            len(canonical(record)) <= 1024 * 1024,
            "Discovery preview exceeds limits; select a package directory",
            "size",
        )
        key = digest(record)
        with self.backend.lock():
            atomic(self.home / "source-discoveries" / (key[7:] + ".json"), record)
        return {**record, "discovery_id": key}

    def discovery(self, key):
        from ._source_records import DIGEST

        require(
            isinstance(key, str) and DIGEST.fullmatch(key), "Invalid discovery identity"
        )
        row = read(self.home / "source-discoveries" / (key[7:] + ".json"))
        require(
            row is not None and digest(row) == key and row.get("kind") == self.kind,
            "Discovery is unavailable or belongs to another manager",
            "not_found",
        )
        return row

    def capture_candidate(
        self, tree, candidate, *, name=None, role="system", definition=None
    ):
        package = package_tree(tree, candidate, name=name, role=role)
        import re

        package_name = name or candidate["name"]
        require(
            isinstance(package_name, str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", package_name),
            "Package name must be a simple resource name",
        )
        with tempfile.TemporaryDirectory(prefix="aixand-package-") as temporary:
            directory = Path(temporary) / package_name
            directory.mkdir()
            directories = set(package["directories"])
            files = {}
            for path, row in package["files"].items():
                parts = path.split("/")
                directories.update("/".join(parts[:i]) for i in range(1, len(parts)))
                files[path] = {
                    "content": base64.b64decode(row["content_b64"], validate=True),
                    "executable": row["executable"],
                }
            self.manager._materialize(directory, sorted(directories), files)
            return self.backend.capture(directory)

    def import_source(
        self, discovery_id, selections=None, *, role="system", guard=None
    ):
        record = self.discovery(discovery_id)
        choices = record["candidates"]
        if selections is None:
            require(
                len(choices) == 1,
                "Select resources when a repository contains several candidates",
            )
            selections = [choices[0]["id"]]
        require(
            isinstance(selections, list)
            and 0 < len(selections) <= 256
            and len(set(selections)) == len(selections),
            "Select distinct resource candidates",
        )
        by_id = {row["id"]: row for row in choices}
        require(
            all(value in by_id for value in selections),
            "Selection is not part of this discovery",
        )
        source = record["source"]
        tree = self.tree(source["source_digest"])
        # Validate every selection before publishing any registration.
        captured = [
            (by_id[key], self.capture_candidate(tree, by_id[key], role=role))
            for key in selections
        ]
        result = []
        for candidate, snapshot in captured:
            publisher = identifier("github-" + source["repository_id"])
            resource = identifier(
                self.kind.rstrip("s") + "-" + digest(candidate["id"])[7:27]
            )
            descriptor = {
                **source,
                "candidate_id": candidate["id"],
                "role": role,
                "label": candidate["name"],
                "interval_hours": 24,
            }
            descriptor["path"] = candidate["path"]
            observed = self.engine.load(publisher, resource, missing_ok=True)
            old_source = (observed or {}).get("source") or {}
            same_selection = (
                old_source.get("ref"),
                old_source.get("path"),
                old_source.get("role"),
            ) == (descriptor["ref"], descriptor["path"], descriptor["role"])
            can_refresh = (
                observed
                and observed["tracking"]
                and not old_source.get("pinned")
                and same_selection
            )
            relation = (
                self.github.relation(old_source, descriptor["commit"])
                if can_refresh
                else None
            )
            with self.backend.lock():
                if guard:
                    guard()
                previous = self.engine.load(publisher, resource, missing_ok=True)
                require(
                    (previous or {}).get("revision")
                    == (observed or {}).get("revision"),
                    "Resource changed while importing; inspect the saved copy",
                    "conflict",
                )
                if previous:
                    old = previous.get("source") or {}
                    require(
                        (old.get("repository_id"), old.get("candidate_id"))
                        == (descriptor["repository_id"], descriptor["candidate_id"]),
                        "Resource identity already belongs to another source",
                        "conflict",
                    )
                    if can_refresh:
                        descriptor.update(
                            interval_hours=old.get("interval_hours", 24),
                            label=old.get("label", descriptor["label"]),
                        )
                        result.append(
                            self.publish(
                                publisher,
                                resource,
                                snapshot,
                                descriptor,
                                previous,
                                relation,
                            )
                        )
                    else:
                        outcome = (
                            "pinned"
                            if old.get("pinned")
                            else "tracking_stopped"
                            if not previous["tracking"]
                            else "source_selection_differs"
                        )
                        result.append(
                            {
                                **self.engine.status(publisher, resource),
                                "outcome": outcome,
                            }
                        )
                else:
                    result.append(
                        self.publish(
                            publisher, resource, snapshot, descriptor, None, "initial"
                        )
                    )
        return {"items": result, "discovery_id": discovery_id}

    def publish(self, publisher, resource, snapshot, source, previous, relation):
        """Called under the manager's identity lock; no network or execution here."""
        validate_source(source)
        self.backend.store(snapshot)
        identity = self.backend.snapshot_digest(snapshot)
        incoming = {
            "digest": identity,
            "revision": None,
            "version": source["commit"][:12],
        }
        if previous is None:
            row = {
                "schema": self.backend.schema,
                "publisher_id": publisher,
                "id": resource,
                "baseline": incoming,
                "upstream": incoming,
                "active_digest": identity,
                "sync_error": None,
                "tracking": True,
                "source": source,
            }
            return {**self.engine.commit(row, None), "outcome": "imported"}
        same_content = identity == previous["upstream"]["digest"]
        if same_content and relation in ("ahead", "identical"):
            # Checks and irrelevant repository commits do not create managed revisions.
            return {**self.engine.status(publisher, resource), "outcome": "unchanged"}
        row = {
            **previous,
            "upstream": incoming,
            "source": source,
            "sync_error": None,
            "source_review": relation not in ("ahead", "identical"),
        }
        ready = getattr(self.backend, "activation_ready", lambda _: True)(snapshot)
        automatic = (
            relation in ("ahead", "identical")
            and not previous.get("hold")
            and not previous.get("migration")
            and not previous.get("source_review")
            and previous["active_digest"] == previous["baseline"]["digest"]
            and ready
        )
        if automatic:
            row.update(active_digest=identity, baseline=incoming)
        return {
            **self.engine.commit(row, previous),
            "outcome": "updated" if automatic else "update_pending",
        }

    def check(
        self, publisher, identifier, *, expected_revision=None, force=False, guard=None
    ):
        resource = identifier
        previous = self.engine.load(publisher, resource)
        if expected_revision is not None:
            require(
                previous["revision"] == expected_revision,
                "Resource changed before source check",
                "conflict",
            )
        source = previous.get("source")
        require(
            source and source.get("kind") == "github", "Resource has no GitHub source"
        )
        if source["pinned"] or not previous["tracking"]:
            return {
                **self.engine.status(publisher, resource),
                "outcome": "pinned" if source["pinned"] else "tracking_stopped",
            }
        old_check = self.check_status(publisher, resource)
        if (
            not force
            and old_check
            and time.time() - old_check.get("checked_at", 0)
            < source.get("interval_hours", 24) * 3600
        ):
            return {**self.engine.status(publisher, resource), "outcome": "not_due"}
        try:
            resolved = self.github.resolve(
                source["url"], ref=source["ref"], path=source["path"], track=True
            )
            require(
                resolved["repository_id"] == source["repository_id"],
                "Repository identity changed",
                "conflict",
            )
            relation = self.github.relation(source, resolved["commit"])
            if relation == "identical":
                snapshot = None
                descriptor = source
            else:
                tree, _ = self.github.snapshot(resolved)
                identity = self.object(tree)
                candidates = discover(self.kind, tree, resolved)
                candidate = next(
                    (row for row in candidates if row["id"] == source["candidate_id"]),
                    None,
                )
                require(
                    candidate is not None,
                    "Publisher removed or renamed this resource; review the source",
                    "not_found",
                )
                snapshot = self.capture_candidate(
                    tree, candidate, role=source.get("role", "system")
                )
                descriptor = {**source, **resolved, "source_digest": identity}
            with self.backend.lock():
                if guard:
                    guard()
                self.engine.guarded(publisher, resource, previous["revision"])
                result = (
                    self.publish(
                        publisher, resource, snapshot, descriptor, previous, relation
                    )
                    if snapshot is not None
                    else {
                        **self.engine.status(publisher, resource),
                        "outcome": "unchanged",
                    }
                )
                if result.get("sync_error"):
                    current = self.engine.load(publisher, resource)
                    result = {
                        **self.engine.commit({**current, "sync_error": None}, current),
                        "outcome": result["outcome"],
                    }
                atomic(
                    self.check_path(publisher, resource),
                    {
                        "checked_at": time.time(),
                        "commit": resolved["commit"],
                        "outcome": result["outcome"],
                        "error": None,
                    },
                )
                return result
        except Exception as error:
            if getattr(error, "code", None) != "cancelled":
                with self.backend.lock():
                    current = self.engine.load(publisher, resource)
                    if current["revision"] == previous["revision"]:
                        diagnostic = {
                            "code": getattr(error, "code", "io"),
                            "message": str(error),
                        }
                        self.engine.commit(
                            {**current, "sync_error": diagnostic}, current
                        )
                        atomic(
                            self.check_path(publisher, resource),
                            {"checked_at": time.time(), "error": diagnostic},
                        )
            raise

    def check_path(self, publisher, resource):
        self.engine.path(publisher, resource)
        return self.home / "source-checks" / publisher / (resource + ".json")

    def import_local(self, path, *, guard=None):
        path = Path(path).expanduser().resolve()
        snapshot = self.backend.capture(path)
        return self.import_captured(path, snapshot, guard=guard)

    def import_captured(self, path, snapshot, *, guard=None):
        """Import already validated bytes without rereading a collection source."""
        path = Path(path).expanduser().resolve()
        name = snapshot["manifest"]["name"]
        resource = "import-" + digest(str(path))[7:31]
        with self.backend.lock():
            if guard:
                guard()
            if self.engine.load("local", resource, missing_ok=True):
                return {
                    **self.engine.status("local", resource),
                    "outcome": "already_registered",
                }
            self.backend.store(snapshot)
            identity = self.backend.snapshot_digest(snapshot)
            incoming = {"digest": identity, "revision": None, "version": None}
            return self.engine.commit(
                {
                    "schema": self.backend.schema,
                    "publisher_id": "local",
                    "id": resource,
                    "baseline": incoming,
                    "upstream": incoming,
                    "active_digest": identity,
                    "sync_error": None,
                    "tracking": False,
                    "label": name,
                },
                None,
            )

    def check_status(self, publisher, resource):
        return read(self.check_path(publisher, resource))

    def create(
        self,
        name,
        *,
        content="",
        description="",
        role="system",
        definition=None,
        publisher="local",
        resource_id=None,
        guard=None,
    ):
        require(
            isinstance(name, str) and 0 < len(name) <= 128,
            "Resource name must be 1–128 characters",
        )
        identifier(publisher)
        resource = identifier(resource_id or slug(name))
        with tempfile.TemporaryDirectory(prefix="skill-create-") as temporary:
            directory = Path(temporary) / name
            require(
                directory.parent == Path(temporary) and name not in (".", ".."),
                "Invalid skill name",
            )
            directory.mkdir()
            require(
                isinstance(description, str) and description.strip(),
                "Describe when to use this skill",
            )
            require(isinstance(content, str), "Skill instructions must be text")
            body = (
                "---\nname: "
                + canonical(name).decode()
                + "\ndescription: "
                + canonical(description).decode()
                + "\n---\n\n"
                + content
            )
            (directory / "SKILL.md").write_text(body, encoding="utf-8")
            snapshot = self.backend.capture(directory)
        with self.backend.lock():
            if guard:
                guard()
            require(
                self.engine.load(publisher, resource, missing_ok=True) is None,
                "A managed resource with this name already exists",
                "conflict",
            )
            self.backend.store(snapshot)
            identity = self.backend.snapshot_digest(snapshot)
            incoming = {"digest": identity, "revision": None, "version": None}
            return self.engine.commit(
                {
                    "schema": self.backend.schema,
                    "publisher_id": publisher,
                    "id": resource,
                    "baseline": incoming,
                    "upstream": incoming,
                    "active_digest": identity,
                    "sync_error": None,
                    "tracking": False,
                    "label": name,
                },
                None,
            )

    def settings(
        self,
        publisher,
        resource,
        *,
        expected_revision,
        pinned=None,
        interval_hours=None,
        label=None,
    ):
        with self.backend.lock():
            previous = self.engine.guarded(publisher, resource, expected_revision)
            row = deepcopy(previous)
            if label is not None:
                require(
                    isinstance(label, str) and 0 < len(label.strip()) <= 128,
                    "Label must be 1–128 characters",
                )
                row["label"] = label.strip()
            if pinned is not None or interval_hours is not None:
                require(row.get("source"), "Resource has no external source")
                if pinned is not None:
                    require(type(pinned) is bool, "Pinned must be boolean")
                    if not pinned:
                        from .github_source import COMMIT

                        require(
                            not COMMIT.fullmatch(row["source"]["ref"]),
                            "Choose a branch source to enable tracking",
                        )
                    row["source"]["pinned"] = pinned
                if interval_hours is not None:
                    require(
                        type(interval_hours) is int and 1 <= interval_hours <= 720,
                        "Check interval must be 1–720 hours",
                    )
                    row["source"]["interval_hours"] = interval_hours
            if row == previous:
                return self.engine.status(publisher, resource)
            return self.engine.commit(row, previous)

    def switch(
        self, publisher, identifier, *, expected_revision, ref, track=None, guard=None
    ):
        previous = self.engine.load(publisher, identifier)
        require(
            previous["revision"] == expected_revision,
            "Resource changed before switching source",
            "conflict",
        )
        source = previous.get("source")
        require(source, "Resource has no GitHub source")
        resolved = self.github.resolve(
            source["url"], ref=ref, path=source["path"], track=track
        )
        require(
            resolved["repository_id"] == source["repository_id"],
            "Repository identity changed",
            "conflict",
        )
        tree, _ = self.github.snapshot(resolved)
        identity = self.object(tree)
        candidates = discover(self.kind, tree, resolved)
        candidate = next(
            (row for row in candidates if row["id"] == source["candidate_id"]), None
        )
        require(
            candidate is not None,
            "Selected resource is absent at this reference",
            "not_found",
        )
        snapshot = self.capture_candidate(
            tree, candidate, role=source.get("role", "system")
        )
        with self.backend.lock():
            if guard:
                guard()
            self.engine.guarded(publisher, identifier, expected_revision)
            return self.publish(
                publisher,
                identifier,
                snapshot,
                {**source, **resolved, "source_digest": identity},
                previous,
                "switched",
            )


class SourceMethods:
    """Small public mixin; no application imports and no constructor side effects."""

    def _sources(self):
        return Sources(self)

    def _source_call(self, method, *args, **kwargs):
        try:
            return getattr(self._sources(), method)(*args, **kwargs)
        except SourceError as error:
            error_class = self._managed().backend.errors[0]
            raise error_class(error.code, str(error)) from None

    def source_capabilities(self):
        from importlib.util import find_spec

        available = (
            getattr(self, "source_client", None) is not None
            or find_spec("github_source") is not None
        )
        return {
            "schema": "resource-sources/capabilities-1",
            "create": True,
            "github": available,
            "private_github": False,
            "source_jobs": True,
            "installation": False,
            "transports": [],
        }

    def create_managed(self, name, **options):
        return self._source_call("create", name, **options)

    def discover_source(self, url, **options):
        return self._source_call("discover", url, **options)

    def import_source(self, discovery_id, selections=None, **options):
        return self._source_call("import_source", discovery_id, selections, **options)

    def import_local(self, path, **options):
        return self._source_call("import_local", path, **options)

    def check_updates(self, publisher, identifier, **options):
        return self._source_call("check", publisher, identifier, **options)

    def source_status(self, publisher, identifier):
        status = self.managed_status(publisher, identifier)
        return {
            "source": status.get("source"),
            "check": self._source_call("check_status", publisher, identifier),
        }

    def source_settings(self, publisher, identifier, **options):
        return self._source_call("settings", publisher, identifier, **options)

    def switch_source(self, publisher, identifier, **options):
        return self._source_call("switch", publisher, identifier, **options)

    def check_due_sources(self):
        import uuid

        if not self.source_capabilities()["github"]:
            return []
        result = []
        sources = self._sources()
        for row in self.managed_summaries():
            source = row.get("source")
            if not source or source["pinned"] or not row["tracking"]:
                continue
            last = sources.check_status(row["publisher_id"], row["id"]) or {}
            interval = source.get("interval_hours", 24) * 3600
            if time.time() - last.get("checked_at", 0) < interval:
                continue
            identity = digest(
                [
                    str(sources.home),
                    self.resource_kind,
                    row["publisher_id"],
                    row["id"],
                    source["commit"],
                    int(time.time() // interval),
                ]
            )
            key = str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
            result.append(
                self.start_source_job(
                    "check",
                    {
                        "publisher": row["publisher_id"],
                        "identifier": row["id"],
                        "force": False,
                    },
                    key=key,
                )
            )
        return result

    def _job_call(self, method, *args, **kwargs):
        from . import source_jobs as jobs

        try:
            return getattr(jobs, method)(self, *args, **kwargs)
        except SourceError as error:
            error_class = self._managed().backend.errors[0]
            raise error_class(error.code, str(error)) from None

    def start_source_job(self, operation, request, *, key=None):
        return self._job_call("start", operation, request, key=key)

    def source_job(self, job_id):
        return self._job_call("status", job_id)

    def source_jobs(self):
        return self._job_call("listing")

    def cancel_source_job(self, job_id):
        return self._job_call("cancel", job_id)
