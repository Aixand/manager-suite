"""Read-only store inspection using the existing content and record validators."""

import stat
from pathlib import Path

from ..applications.resolution import capture
from ..errors import PromptError, diagnostic, require
from ..managed.metadata import describe
from ..prompts.definitions import DIGEST, release_ref
from ..storage.inventory import scan

SECTIONS = {
    "objects": 2,
    "releases": 3,
    "managed": 3,
    "managed-history": 4,
    "sources": 2,
    "applications": 2,
}


class _StoreCheck:
    def __init__(self, store, registry, managed):
        self.store, self.registry, self.managed = store, registry, managed
        self.home = store.home
        self.findings, self.seen_findings = [], set()
        self.complete = True
        self.references_complete = True
        self.counts = dict.fromkeys(SECTIONS, 0)

    def finding(self, category, code, path, message, *, error=None):
        key = category, code, str(path), message
        if key in self.seen_findings:
            return
        self.seen_findings.add(key)
        row = {
            "severity": "error" if category in {"corruption", "access"} else "warning",
            "category": category,
            "code": code,
            "path": str(path),
            "message": message,
        }
        if error is not None:
            row["diagnostic"] = diagnostic(error)
        self.findings.append(row)

    def inaccessible(self, error):
        self.complete = False
        self.finding(
            "access",
            "read_failed",
            getattr(error, "filename", None) or self.home,
            str(error),
            error=error,
        )

    def inventory(self):
        found, errors = scan(self.home, SECTIONS)
        for error in errors:
            self.inaccessible(error)
        return found

    def attempt(self, path, operation):
        try:
            return operation()
        except (PromptError, OSError) as error:
            if isinstance(error, OSError) or error.code == "io":
                self.complete = False
                category = "access"
            else:
                category = "corruption"
            self.finding(category, "record_invalid", path, str(error), error=error)
            self.references_complete = False
            return None

    def result(self, before, after):
        stable = before == after
        if not stable:
            self.finding(
                "concurrent_change",
                "store_changed",
                self.home,
                "Store files changed during inspection; retry when writers are idle",
            )
        errors = sum(row["severity"] == "error" for row in self.findings)
        self.counts.update(errors=errors, warnings=len(self.findings) - errors)
        return {
            "schema": "prompt-manager/store-check-1",
            "store": str(self.home),
            "exists": self.home in before,
            "ok": not errors and stable and self.complete,
            "complete": self.complete,
            "stable": stable,
            "references_complete": self.references_complete,
            "counts": self.counts,
            "findings": self.findings,
        }

    def run(self, include_external):
        before = self.inventory()
        if not self.complete:
            # Do not let normal loaders follow unsupported links/special files.
            self.references_complete = False
            return self.result(before, self.inventory())
        records = {section: [] for section in SECTIONS}
        artifacts = []
        for path in sorted(before):
            if path == self.home or stat.S_ISDIR(before[path][0]):
                continue
            parts = path.relative_to(self.home).parts
            if path.name.startswith((".registry-", ".publish-")):
                artifacts.append(path)
                continue
            if path.suffix != ".json":
                continue
            if len(parts) != SECTIONS[parts[0]]:
                self.finding(
                    "corruption", "record_layout", path, "Record is outside its expected layout"
                )
                self.references_complete = False
                continue
            records[parts[0]].append(path)
        self.counts.update({section: len(paths) for section, paths in records.items()})
        objects, present_objects = set(), set()
        descriptions = {}
        for path in records["objects"]:
            identity = "sha256:" + path.stem
            present_objects.add(identity)
            snapshot = self.attempt(path, lambda: self.managed.backend.snapshot(identity))
            if snapshot is not None:
                objects.add(identity)
                descriptions[identity] = describe(snapshot, identity)

        def check_description(row, path):
            summary = row.get("content_metadata")
            expected = descriptions.get(row["active_digest"])
            if summary is not None and expected is not None and summary != expected:
                self.finding(
                    "corruption",
                    "content_metadata_mismatch",
                    path,
                    "Managed descriptive metadata does not match accepted content",
                )
                self.references_complete = False

        histories = {}
        for path in records["managed-history"]:
            publisher, package = path.parent.parent.name, path.parent.name
            revision = "sha256:" + path.stem
            row = self.attempt(path, lambda: self.managed.load(publisher, package, revision))
            if row is not None:
                histories[publisher, package, revision] = row
                check_description(row, path)
        currents = {}
        for path in records["managed"]:
            publisher, package = path.parent.name, path.stem
            row = self.attempt(path, lambda: self.managed.load(publisher, package))
            if row is not None:
                currents[publisher, package] = row
                check_description(row, path)
                if histories.get((publisher, package, row["revision"])) != row:
                    self.finding(
                        "corruption",
                        "current_history_mismatch",
                        path,
                        "Current state has no identical immutable history record",
                    )
                    self.references_complete = False
        required_objects, reachable_objects, reachable_history = set(), set(), set()

        def identities(row):
            values = {row["active_digest"], row["baseline"]["digest"], row["upstream"]["digest"]}
            if row.get("migration"):
                values.add(row["migration"]["legacy_digest"])
            return values

        for key, row in histories.items():
            required_objects.update(identities(row))
            previous = row["previous"]
            path = self.home / "managed-history" / key[0] / key[1] / (key[2][7:] + ".json")
            parent = histories.get((key[0], key[1], previous)) if previous else None
            if previous and parent is None:
                self.finding(
                    "corruption",
                    "history_link_missing",
                    path,
                    "Previous history record is missing or invalid",
                )
                self.references_complete = False
            elif (parent and parent["sequence"] != row["sequence"] - 1) or (
                not previous and row["sequence"] != 1
            ):
                self.finding(
                    "corruption",
                    "history_sequence",
                    path,
                    "History sequence does not match its parent",
                )
                self.references_complete = False

        def follow(publisher, package, row):
            seen = set()
            while row is not None:
                key = publisher, package, row["revision"]
                if key in seen:
                    self.finding(
                        "corruption",
                        "history_cycle",
                        self.home / "managed-history" / publisher / package,
                        "History contains a cycle",
                    )
                    self.references_complete = False
                    break
                if key in reachable_history:
                    break
                seen.add(key)
                reachable_history.add(key)
                reachable_objects.update(identities(row))
                row = (
                    histories.get((publisher, package, row["previous"]))
                    if row["previous"]
                    else None
                )

        for (publisher, package), row in currents.items():
            required_objects.update(identities(row))
            follow(publisher, package, row)
        for path in records["releases"]:
            reference = path.parent.name + "@" + path.stem
            loaded = self.attempt(path, lambda: self.store.load(reference))
            if loaded is not None:
                reachable_objects.add(loaded[1]["digest"])
        for path in records["sources"]:
            source = self.attempt(path, lambda: self.registry.get("sources", path.stem))
            if source is not None and include_external:
                for location in source["paths"]:
                    try:
                        available = Path(location).is_dir()
                    except OSError:
                        available = False
                    if not available:
                        self.finding(
                            "availability",
                            "source_unavailable",
                            location,
                            f"External source {source['id']} is unavailable",
                        )
        for path in records["applications"]:
            application = self.attempt(path, lambda: self.registry.get("applications", path.stem))
            if application is None:
                continue
            for logical, target in application["prompts"].items():
                if target["kind"] == "managed" and "revision" in target:
                    row = histories.get(
                        (target["publisher_id"], target["package_id"], target["revision"])
                    )
                    if row is not None:
                        follow(target["publisher_id"], target["package_id"], row)
                if target["kind"] == "release":
                    _, version = release_ref(target["reference"].partition("/")[0])
                    if DIGEST.fullmatch(version):
                        reachable_objects.add(version)
                        required_objects.add(version)
                if include_external or target["kind"] != "draft":
                    try:
                        capture(
                            self.store, [], self.registry, self.managed, application["id"], logical
                        )
                    except (PromptError, OSError) as error:
                        self.finding(
                            "availability",
                            "mapping_unresolved",
                            path,
                            f"{application['id']}/{logical}: {error}",
                            error=error,
                        )
        for identity in sorted(required_objects - present_objects):
            self.finding(
                "corruption",
                "object_missing",
                self.home / "objects" / (identity[7:] + ".json"),
                "Referenced content object is missing",
            )
        after = self.inventory()
        stable = before == after
        if stable and self.complete and self.references_complete:
            for identity in sorted(objects - reachable_objects):
                self.finding(
                    "unreferenced",
                    "unreferenced_object",
                    self.home / "objects" / (identity[7:] + ".json"),
                    "Valid object is not reachable from current state, history roots, releases, or pinned mappings",
                )
            for key in sorted(histories.keys() - reachable_history):
                self.finding(
                    "unreferenced",
                    "unreferenced_history",
                    self.home / "managed-history" / key[0] / key[1] / (key[2][7:] + ".json"),
                    "Valid history record is not reachable from current state or pinned mappings",
                )
        for path in artifacts:
            self.finding(
                "unreferenced",
                "temporary_file",
                path,
                "Temporary file may belong to an active or interrupted write; no action taken",
            )
        return self.result(before, after)


def check_store(store, registry, managed, *, include_external=True):
    require(type(include_external) is bool, "include_external must be a boolean")
    return _StoreCheck(store, registry, managed).run(include_external)
