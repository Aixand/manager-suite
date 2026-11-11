"""Inherited launch labels and private metadata, independent of provider identity."""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import inspect
import json
import os
from pathlib import Path
import time
import unicodedata

from agent_manager.errors import Error
from agent_manager.storage.files import atomic
from agent_manager.storage.locks import locks
from agent_manager.validation import name


def attributes(value, kind, *, internal=False):
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise Error(f"{kind} must be a string mapping")
    if len(value) > 64:
        raise Error(f"{kind} accepts at most 64 entries")
    result = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise Error(f"{kind} keys must be nonempty strings")
        if not isinstance(item, str):
            raise Error(f"{kind} values must be strings")
        if not internal and key.startswith("agent_manager."):
            raise Error("The agent_manager.* attribute namespace is reserved")
        for text, maximum in ((key, 64), (item, 256 if kind == "labels" else 4096)):
            try:
                valid = len(text.encode("utf-8")) <= maximum and not any(
                    unicodedata.category(char) == "Cc" for char in text)
            except UnicodeEncodeError:
                valid = False
            if not valid:
                raise Error(f"Invalid {kind} key or value (UTF-8 byte limit {maximum}; no control characters)")
        result[key] = item
    return result


def merge(kind, *layers, internal=False):
    result = {}
    for layer in layers:
        result.update(attributes(layer, kind, internal=internal))
    return attributes(result, kind, internal=internal)


class Context:
    """A copied snapshot. Accessors return copies so defaults cannot be mutated."""
    def __init__(self, labels=None, metadata=None):
        self._labels = attributes(labels, "labels")
        self._metadata = attributes(metadata, "metadata")

    def __repr__(self):
        return f"Context(labels={self._labels!r}, metadata_entries={len(self._metadata)})"

    @property
    def labels(self):
        return dict(self._labels)

    @property
    def metadata(self):
        return dict(self._metadata)

    def resolve(self, *, labels=None, metadata=None, inherit_labels=True, inherit_metadata=True):
        if not isinstance(inherit_labels, bool) or not isinstance(inherit_metadata, bool):
            raise Error("Context inheritance flags must be booleans")
        return Context(merge("labels", self._labels if inherit_labels else {}, labels),
                       merge("metadata", self._metadata if inherit_metadata else {}, metadata))

    def as_dict(self):
        return {"labels": self.labels, "metadata": self.metadata}


def environment(kind):
    variable = "AGENT_MANAGER_" + kind.upper()
    if variable not in os.environ:
        return {}
    try:
        value = json.loads(os.environ[variable])
    except (ValueError, TypeError):
        raise Error(f"{variable} must contain a JSON string mapping") from None
    return attributes(value, kind)


def defaults(manager):
    return Context(merge("labels", manager.config.get("labels"), environment("labels")),
                   merge("metadata", manager.config.get("metadata"), environment("metadata")))


def project_context(root, *, project_id=None, project_name=None):
    """Use the original root, never an agent's disposable working copy."""
    root = Path(root).expanduser().resolve()
    identity = project_id or "root:" + hashlib.sha256(os.path.normcase(str(root)).encode()).hexdigest()
    return Context({"project.id": identity, "project.name": project_name or root.name or str(root)},
                   {"project.root": str(root)}).as_dict()


SESSION_KINDS = frozenset({"server", "agent", "shell", "job"})


def session_context(context, kind, *, service=None, thread_id=None):
    """Classify a new process, replacing inherited process-specific attributes."""
    if not isinstance(kind, str) or kind not in SESSION_KINDS:
        raise Error("Session kind must be server, agent, shell, or job")
    labels, metadata = context.labels, context.metadata
    labels["session.kind"] = kind
    labels.pop("service.name", None)
    if kind == "server":
        if not isinstance(service, str) or not service:
            raise Error("Server sessions require a service name")
        labels["service.name"] = service
    elif service is not None:
        raise Error("Service names belong to server sessions")
    # Relationships describe this process, never a parent's terminal attachment.
    for key, value in (("session.thread_id", thread_id),):
        metadata.pop(key, None)
        if value is not None:
            metadata[key] = value
    return Context(labels, metadata)


def thread_context(context):
    """Logical conversations retain task context, without process classification."""
    labels, metadata = context.labels, context.metadata
    for key in ("session.kind", "service.name"):
        labels.pop(key, None)
    for key in ("session.thread_id",):
        metadata.pop(key, None)
    return Context(labels, metadata)


def application_context(app, feature, *, project=None, project_name=None, inherited=None, labels=None, metadata=None,
                        session_kind=None, service_name=None):
    """Build context at an application boundary, retaining an initiating app."""
    base = Context(**(inherited or {}))
    source = base.labels.get("app")
    identity = {"app": app, "feature": feature}
    if "origin.app" in base.labels:
        identity["origin.app"] = base.labels["origin.app"]
    elif source and source != app:
        identity["origin.app"] = source
    if project is not None:
        root = str(Path(project).expanduser().resolve())
        same = base.metadata.get("project.root") in (None, root)
        configured = environment("labels")
        project_data = project_context(root,
            project_id=(base.labels.get("project.id") if same else None) or configured.get("project.id"),
            project_name=project_name or (base.labels.get("project.name") if same else None) or configured.get("project.name"))
        base = base.resolve(**project_data)
    result = base.resolve(labels=merge("labels", labels, identity), metadata=metadata)
    if session_kind is not None:
        result = session_context(result, session_kind, service=service_name)
    elif service_name is not None:
        raise Error("Service names require a server session kind")
    return result.as_dict()


def from_record(record):
    launch = record.get("launch", {})
    return {"labels": dict(record.get("labels", launch.get("labels", {}))),
            "metadata": dict(record.get("metadata", launch.get("metadata", {})))}


def require_metadata(runtime, metadata):
    if not metadata:
        return
    start = getattr(runtime, "start", None)
    if start is not None:
        try:
            parameters = inspect.signature(start).parameters.values()
        except (TypeError, ValueError):
            return
        if not any(p.name == "metadata" or p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters):
            raise Error("Session metadata requires an updated Undercurrent Python SDK")


def key_values(items, kind):
    result = {}
    for item in items or ():
        key, separator, value = item.partition("=")
        if not separator:
            raise Error(f"--{kind[:-1] if kind == 'labels' else kind} requires KEY=VALUE")
        result[key] = value
    return attributes(result, kind)


class ThreadStore:
    """Known managed threads; private context is separate from list projections."""
    def __init__(self, manager):
        self.manager = manager
        self.root = manager.root / "thread-context"

    def path(self, profile, thread_id):
        name(profile)
        name(thread_id)
        from agent_manager.profiles.identities import storage_key
        return self.root / storage_key(self.manager.root, profile) / (thread_id + ".json")

    def read(self, profile, thread_id):
        path = self.path(profile, thread_id)
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_text())
            from agent_manager.profiles.identities import storage_key, resolve_record
            if record["environment_id"] != storage_key(self.manager.root, profile) or record["thread_id"] != thread_id:
                raise ValueError
            Context(record["labels"], record["metadata"])
            record = resolve_record(self.manager.state(), record)
            return record
        except (OSError, ValueError, KeyError, TypeError, Error):
            raise Error("Invalid managed thread context") from None

    def save(self, profile, thread_id, context, *, cwd):
        profile = self.manager.resolve_profile(profile)
        path = self.path(profile, thread_id)
        with locks(self.manager.lock_root, "thread-context-" + profile.environment_id + "-" + thread_id, wait=True):
            previous = self.read(profile, thread_id)
            if previous:
                if any(previous[key] != value for key, value in context.as_dict().items()):
                    raise Error("Managed thread context already exists with different attributes")
                return previous
            record = {"provider": self.manager.provider, "profile": profile, "profile_id": profile.profile_id, "environment_id": profile.environment_id, "thread_id": thread_id,
                      "cwd": str(cwd),
                      "created_at_millis": int(time.time() * 1000), **context.as_dict()}
            atomic(path, json.dumps(record, sort_keys=True).encode())
            return record

    def list(self, *, profile=None, labels=None, limit=250):
        selected = attributes(labels, "labels", internal=True)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise Error("limit must be an integer between 1 and 1000")
        if profile is not None:
            name(profile)
        from agent_manager.profiles.identities import storage_key
        paths = (self.root / storage_key(self.manager.root, profile)).glob("*.json") if profile else self.root.glob("*/*.json")
        result = []
        for path in paths:
            from agent_manager.profiles.identities import for_environment
            row = self.read(for_environment(self.manager.state(), path.parent.name), path.stem)
            if row and all(row["labels"].get(key) == value for key, value in selected.items()):
                result.append({key: value for key, value in row.items() if key != "metadata"})
        result.sort(key=lambda row: (-row["created_at_millis"], row["profile"], row["thread_id"]))
        return result[:limit]
