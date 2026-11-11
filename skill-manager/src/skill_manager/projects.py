"""Guarded project deployments with immutable plans and retained recovery data."""

import contextlib
import os
import re
import secrets
from copy import deepcopy
from pathlib import Path

from aixand_filesystem import lock_file

from . import fs
from .codec import DIGEST, canonical, digest, skill_name, source_id
from .contracts import SkillReference
from .errors import SkillError, require
from .store import json_read


class Projects:
    def __init__(self, manager, root):
        self.manager = manager
        supplied = fs.absolute(root)
        with fs.directory(supplied):
            self.root = supplied.resolve()
        self.identifier = digest(str(self.root))[7:]
        self.home = manager.home / "projects" / self.identifier
        self.control = self.root / ".skill-manager"
        self.manifest = self.control / "project.json"
        self.owner_path = self.control / "local.json"
        self.pending_path = self.control / "pending.json"

    def _pending(self):
        marker = self._read(self.pending_path)
        if marker is None:
            return None
        require(
            isinstance(marker, dict) and set(marker) == {"store_id", "id"},
            "Invalid pending project operation",
            "integrity",
        )
        if marker["store_id"] == self.manager.store_id:
            row = self._read(self._operation_path(marker["id"]))
            if row is not None and row["status"] in ("completed", "rolled_back"):
                return None
        return marker["id"]

    def _read(self, path):
        try:
            return json_read(path)
        except FileNotFoundError:
            return None

    def _directory(self, value):
        parts = fs.relative(value)
        require(
            fs.path_key(parts[0]) not in {".skill-manager", ".git", ".hg", ".svn"},
            "Skill directory cannot overlap project control files",
        )
        return self.root.joinpath(*parts)

    def _entry(self, row):
        require(
            isinstance(row, dict)
            and set(row) == {"publisher_id", "skill_id", "name", "revision", "digest"},
            "Invalid project skill entry",
            "integrity",
        )
        source_id(row["publisher_id"])
        source_id(row["skill_id"])
        skill_name(row["name"])
        require(
            all(
                isinstance(row[key], str) and DIGEST.fullmatch(row[key])
                for key in ("revision", "digest")
            ),
            "Invalid project skill content identity",
            "integrity",
        )
        return row["publisher_id"] + "/" + row["skill_id"]

    def _validate_state(self, state):
        require(
            isinstance(state, dict)
            and set(state)
            == {"schema", "project", "skills_dir", "connections", "revision"}
            and state["schema"] == "skill-manager/project-state-1"
            and state["project"] == str(self.root)
            and state["revision"]
            == digest({k: v for k, v in state.items() if k != "revision"})
            and isinstance(state["connections"], dict)
            and len(state["connections"]) <= 256,
            "Invalid project receipt",
            "integrity",
        )
        self._directory(state["skills_dir"])
        names = []
        for key, row in state["connections"].items():
            require(
                self._entry(row) == key,
                "Project connection identity mismatch",
                "integrity",
            )
            names.append(row["name"])
        require(
            len(names) == len({fs.path_key(name) for name in names}),
            "Selected skills collide on their native directory names",
            "conflict",
        )
        fs.check_paths([], dict.fromkeys(names))
        return state

    def _state(self):
        state = self._read(self.home / "state.json")
        return self._validate_state(state) if state is not None else None

    def _portable(self, state):
        return {
            "schema": "skill-manager/project-1",
            "skills_dir": state["skills_dir"],
            "skills": [
                state["connections"][key] for key in sorted(state["connections"])
            ],
        }

    def _owner(self):
        return {
            "schema": "skill-manager/project-owner-1",
            "store_id": self.manager.store_id,
            "project_id": self.identifier,
        }

    def _observe(self, path, name):
        try:
            before = path.lstat()
        except FileNotFoundError:
            return {"exists": False}
        identity = [before.st_dev, before.st_ino]
        try:
            captured = fs.capture(path, name=name)
            after = path.lstat()
            require(
                identity == [after.st_dev, after.st_ino],
                "Destination changed while inspecting",
                "conflict",
            )
            return {
                "exists": True,
                "identity": identity,
                "digest": captured["digest"],
                "valid": captured["error"] is None,
            }
        except (SkillError, OSError) as error:
            return {
                "exists": True,
                "identity": identity,
                "valid": False,
                "error": str(error),
            }

    def _selector(self, selector):
        if hasattr(selector, "reference"):
            selector = selector.reference
        if isinstance(selector, dict):
            selector = SkillReference.from_dict(selector)
        return selector

    def _selected(self, selector):
        selector = self._selector(selector)
        if isinstance(selector, SkillReference):
            handle = self.manager.open_skill(selector)
        elif isinstance(selector, str) and "/" in selector:
            require(selector.count("/") == 1, "Use publisher/skill-id")
            handle = self.manager.skill(*selector.split("/"))
        else:
            require(isinstance(selector, str) and selector, "Select a skill identity")
            matches = []
            for row in self.manager.managed_catalog():
                if "error" in row:
                    continue
                resolved = self.manager.resolve_managed(row["publisher_id"], row["id"])
                if selector in (row["id"], resolved["name"]):
                    matches.append((row["publisher_id"], row["id"]))
            require(
                len(matches) == 1,
                "Skill name is missing or ambiguous; use publisher/skill-id",
                "conflict",
            )
            handle = self.manager.skill(*matches[0])
        return handle.resolve()

    def _row(self, resolved):
        return {
            "publisher_id": resolved.reference.publisher_id,
            "skill_id": resolved.reference.skill_id,
            "name": resolved.name,
            "revision": resolved.revision,
            "digest": resolved.digest,
        }

    def _resolve(self, row):
        handle = self.manager.skill(row["publisher_id"], row["skill_id"])
        try:
            resolved = handle.resolve(revision=row["revision"])
        except SkillError:
            resolved = handle.resolve()
        require(
            resolved.digest == row["digest"] and resolved.name == row["name"],
            "Pinned library content is unavailable; explicitly import the required content",
            "not_found",
        )
        return resolved

    def _base(self, skills_dir=None, *, restore=False, adopt=False):
        require(
            self._pending() is None,
            "Project has an interrupted operation; recover it first",
            "conflict",
        )
        state = self._state()
        actual = self._read(self.manifest)
        owner = self._read(self.owner_path)
        require(
            owner in (None, self._owner()) or restore and adopt,
            "Project belongs to another store; explicit restore/adoption is required",
            "conflict",
        )
        if not restore:
            require(
                actual == (self._portable(state) if state else None),
                "Project manifest differs from the receipt; review or restore it explicitly",
                "conflict",
            )
        if state:
            require(
                skills_dir is None or skills_dir == state["skills_dir"],
                "Project already uses a different skill directory",
                "conflict",
            )
            return state, actual, owner
        require(skills_dir is not None, "Specify skills_dir for the initial connection")
        self._directory(skills_dir)
        return {**self._make_state(skills_dir, {}), "revision": None}, actual, owner

    def _make_state(self, skills_dir, connections):
        state = {
            "schema": "skill-manager/project-state-1",
            "project": str(self.root),
            "skills_dir": skills_dir,
            "connections": connections,
        }
        return self._validate_state({**state, "revision": digest(state)})

    def _seal(self, action, before, actual, owner, after, changes):
        observed = self._state()
        require(
            (observed["revision"] if observed else None) == before["revision"],
            "Project receipt changed",
            "conflict",
        )
        names = [
            (row["old"] or row["desired"])["name"]
            for row in changes
            if row["kind"] != "forget"
        ]
        require(
            len(names) == len(set(names)),
            "Disconnect/forget the old identity before rebinding its native directory",
            "conflict",
        )
        plan = {
            "schema": "skill-manager/project-plan-1",
            "action": action,
            "project": str(self.root),
            "intent": secrets.token_hex(16),
            "store_id": self.manager.store_id,
            "before": observed["revision"] if observed else None,
            "before_state": observed,
            "manifest_before": actual,
            "owner_before": owner,
            "after": after,
            "changes": changes,
        }
        plan["token"] = digest(plan)
        with self.manager._store.lock():
            self.manager._store.put_json(
                self.home / "plans" / (plan["token"][7:] + ".json"),
                plan,
                immutable=True,
            )
        return deepcopy(plan)

    def preview_connect(self, selections, *, skills_dir=None, adopt=False):
        require(
            isinstance(selections, (list, tuple)) and 0 < len(selections) <= 256,
            "Select skills to connect",
        )
        state, actual, owner = self._base(skills_dir)
        connections = deepcopy(state["connections"])
        changes, seen = [], set()
        for selector in selections:
            row = self._row(self._selected(selector))
            key = self._entry(row)
            require(key not in seen, "Select each skill once")
            seen.add(key)
            old = connections.get(key)
            if old is not None:
                require(
                    old["name"] == row["name"],
                    "Disconnect the old native name before reconnecting",
                    "conflict",
                )
                # Existing connections are updated through preview_update explicitly.
                row = old
            path = self._directory(state["skills_dir"]) / row["name"]
            observed = self._observe(path, row["name"])
            if old is None:
                require(
                    not observed["exists"]
                    or adopt
                    and observed.get("valid")
                    and observed.get("digest") == row["digest"],
                    "Destination already exists; adoption requires identical content",
                    "conflict",
                )
                changes.append(
                    {
                        "key": key,
                        "kind": "adopt" if observed["exists"] else "install",
                        "old": None,
                        "desired": row,
                        "observed": observed,
                    }
                )
            else:
                require(
                    observed.get("valid") and observed.get("digest") == old["digest"],
                    "Connected skill has local changes; preserve them before reconnecting",
                    "conflict",
                )
            connections[key] = row
        after = self._make_state(state["skills_dir"], connections)
        return self._seal("connect", state, actual, owner, after, changes)

    def _keys(self, state, selections):
        if selections is None:
            return list(state["connections"])
        require(
            isinstance(selections, (list, tuple)) and 0 < len(selections) <= 256,
            "Select project connections",
        )
        keys = []
        for value in selections:
            value = self._selector(value)
            if isinstance(value, SkillReference):
                require(
                    value.store == str(self.manager.home),
                    "Reference belongs to another store",
                    "conflict",
                )
                value = value.publisher_id + "/" + value.skill_id
            require(isinstance(value, str) and value, "Select a connection identity")
            matches = [
                key
                for key, row in state["connections"].items()
                if value in (key, row["skill_id"], row["name"])
            ]
            require(
                len(matches) == 1,
                "Connection is missing or ambiguous; use publisher/skill-id",
                "conflict",
            )
            require(matches[0] not in keys, "Select each connection once")
            keys.append(matches[0])
        return keys

    def preview_update(self, selections=None):
        state, actual, owner = self._base()
        connections, changes = deepcopy(state["connections"]), []
        for key in self._keys(state, selections):
            old = connections[key]
            observed = self._observe(
                self._directory(state["skills_dir"]) / old["name"], old["name"]
            )
            require(
                observed.get("valid") and observed.get("digest") == old["digest"],
                "Project skill is modified or missing; preserve edits before updating",
                "conflict",
            )
            row = self._row(
                self.manager.skill(old["publisher_id"], old["skill_id"]).resolve()
            )
            require(
                row["name"] == old["name"],
                "Native name changed; explicitly disconnect/reconnect",
                "conflict",
            )
            if row["digest"] != old["digest"]:
                changes.append(
                    {
                        "key": key,
                        "kind": "replace",
                        "old": old,
                        "desired": row,
                        "observed": observed,
                    }
                )
                connections[key] = row
        return self._seal(
            "update",
            state,
            actual,
            owner,
            self._make_state(state["skills_dir"], connections),
            changes,
        )

    def preview_disconnect(self, selections, *, forget=False):
        require(selections is not None, "Explicitly select connections to disconnect")
        state, actual, owner = self._base()
        connections, changes = deepcopy(state["connections"]), []
        for key in self._keys(state, selections):
            old = connections.pop(key)
            observed = self._observe(
                self._directory(state["skills_dir"]) / old["name"], old["name"]
            )
            require(
                forget
                or not observed["exists"]
                or observed.get("valid")
                and observed.get("digest") == old["digest"],
                "Modified project files are preserved; save a variant or explicitly forget the connection",
                "conflict",
            )
            changes.append(
                {
                    "key": key,
                    "kind": "forget" if forget or not observed["exists"] else "remove",
                    "old": old,
                    "desired": None,
                    "observed": observed,
                }
            )
        return self._seal(
            "forget" if forget else "disconnect",
            state,
            actual,
            owner,
            self._make_state(state["skills_dir"], connections),
            changes,
        )

    def preview_restore(self, *, adopt=False):
        manifest = self._read(self.manifest)
        if manifest is None:
            existing = self._state()
            require(
                existing is not None, "No manifest or receipt is available", "not_found"
            )
            manifest = self._portable(existing)
        require(
            isinstance(manifest, dict)
            and set(manifest) == {"schema", "skills_dir", "skills"}
            and manifest["schema"] == "skill-manager/project-1"
            and isinstance(manifest["skills"], list),
            "A valid portable project manifest is required",
        )
        state, actual, owner = self._base(
            manifest["skills_dir"], restore=True, adopt=adopt
        )
        connections, changes = {}, []
        for row in manifest["skills"]:
            key = self._entry(row)
            require(key not in connections, "Duplicate project skill")
            resolved = self._resolve(row)
            selected = self._row(resolved)
            connections[key] = selected
            old = state["connections"].get(key)
            observed = self._observe(
                self._directory(state["skills_dir"]) / row["name"], row["name"]
            )
            if old:
                require(
                    old["name"] == row["name"]
                    and (
                        not observed["exists"]
                        or observed.get("valid")
                        and observed.get("digest") == old["digest"]
                    ),
                    "Restore would overwrite modified project content",
                    "conflict",
                )
                if not observed["exists"] or old["digest"] != selected["digest"]:
                    changes.append(
                        {
                            "key": key,
                            "kind": "replace" if observed["exists"] else "install",
                            "old": old if observed["exists"] else None,
                            "desired": selected,
                            "observed": observed,
                        }
                    )
                else:
                    connections[key] = old
            else:
                require(
                    not observed["exists"]
                    or adopt
                    and observed.get("valid")
                    and observed.get("digest") == selected["digest"],
                    "Existing content requires explicit identical adoption",
                    "conflict",
                )
                changes.append(
                    {
                        "key": key,
                        "kind": "adopt" if observed["exists"] else "install",
                        "old": None,
                        "desired": selected,
                        "observed": observed,
                    }
                )
        for key, old in state["connections"].items():
            if key not in connections:
                observed = self._observe(
                    self._directory(state["skills_dir"]) / old["name"], old["name"]
                )
                require(
                    not observed["exists"]
                    or observed.get("valid")
                    and observed.get("digest") == old["digest"],
                    "Restore would remove modified content",
                    "conflict",
                )
                changes.append(
                    {
                        "key": key,
                        "kind": "remove" if observed["exists"] else "forget",
                        "old": old,
                        "desired": None,
                        "observed": observed,
                    }
                )
        return self._seal(
            "restore",
            state,
            actual,
            owner,
            self._make_state(state["skills_dir"], connections),
            changes,
        )

    @contextlib.contextmanager
    def _lock(self):
        # Store first, then project: all project writers follow this order.
        with self.manager._store.lock():
            fs.make_directories(self.control)
            with fs.directory(self.control) as parent:
                if os.name == "nt":
                    stream = (Path(parent) / "connection.lock").open("a+b")
                else:
                    stream = os.fdopen(
                        os.open(
                            "connection.lock",
                            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                            0o600,
                            dir_fd=parent,
                        ),
                        "a+b",
                    )
                with stream:
                    lock_file(stream, blocking=True)
                    yield

    def _plan(self, plan):
        require(
            isinstance(plan, dict)
            and set(plan)
            == {
                "schema",
                "action",
                "project",
                "store_id",
                "intent",
                "before",
                "before_state",
                "manifest_before",
                "owner_before",
                "after",
                "changes",
                "token",
            }
            and plan.get("schema") == "skill-manager/project-plan-1"
            and isinstance(plan.get("token"), str)
            and DIGEST.fullmatch(plan["token"]),
            "Expected a sealed project plan",
        )
        saved = self._read(self.home / "plans" / (plan["token"][7:] + ".json"))
        require(
            saved == plan
            and plan["token"] == digest({k: v for k, v in plan.items() if k != "token"})
            and plan["store_id"] == self.manager.store_id
            and plan["project"] == str(self.root),
            "Project plan is altered or belongs to another project/store",
            "conflict",
        )
        self._validate_state(plan["after"])
        before = plan.get("before_state")
        if before is not None:
            self._validate_state(before)
            require(
                before["skills_dir"] == plan["after"]["skills_dir"],
                "Plan cannot move the skills directory",
                "integrity",
            )
        require(
            (before["revision"] if before else None) == plan["before"],
            "Plan baseline mismatch",
            "integrity",
        )
        require(
            isinstance(plan["changes"], list) and len(plan["changes"]) <= 256,
            "Invalid plan changes",
            "integrity",
        )
        transition = deepcopy(before["connections"]) if before else {}
        seen = set()
        for change in plan["changes"]:
            require(
                isinstance(change, dict)
                and set(change) == {"key", "kind", "old", "desired", "observed"},
                "Invalid plan change",
                "integrity",
            )
            key, kind = change["key"], change["kind"]
            require(
                isinstance(key, str)
                and key not in seen
                and kind in ("install", "adopt", "replace", "remove", "forget"),
                "Invalid/duplicate plan action",
                "integrity",
            )
            seen.add(key)
            old, desired = change["old"], change["desired"]
            for row in (old, desired):
                if row is not None:
                    require(
                        self._entry(row) == key, "Change identity mismatch", "integrity"
                    )
            observation = change["observed"]
            require(
                isinstance(observation, dict)
                and type(observation.get("exists")) is bool,
                "Invalid destination observation",
                "integrity",
            )
            if kind in ("replace", "remove", "forget"):
                require(
                    old is not None and transition.get(key) == old,
                    "Change does not own this installed skill",
                    "integrity",
                )
            else:
                require(
                    old is None
                    and desired is not None
                    and (
                        key not in transition
                        or kind == "install"
                        and not observation["exists"]
                        and transition[key]["name"] == desired["name"]
                    ),
                    "Invalid new destination claim",
                    "integrity",
                )
            require(
                (desired is None) == (kind in ("remove", "forget")),
                "Invalid desired action",
                "integrity",
            )
            if old is not None and desired is not None:
                require(
                    old["name"] == desired["name"],
                    "Plan cannot rename an installed package",
                    "integrity",
                )
            if desired is None:
                transition.pop(key, None)
            else:
                transition[key] = desired
        require(
            transition == plan["after"]["connections"],
            "Plan transition differs from receipt",
            "integrity",
        )
        return saved

    def _guard(self, plan):
        require(
            self._pending() is None,
            "Project has an interrupted operation; recover it first",
            "conflict",
        )
        state = self._state()
        require(
            (state["revision"] if state else None) == plan["before"],
            "Stale project receipt; preview again",
            "conflict",
        )
        require(
            self._read(self.manifest) == plan["manifest_before"]
            and self._read(self.owner_path) == plan["owner_before"],
            "Project control files changed; preview again",
            "conflict",
        )
        for change in plan["changes"]:
            row = change["old"] or change["desired"]
            path = self._directory(plan["after"]["skills_dir"]) / row["name"]
            require(
                self._observe(path, row["name"]) == change["observed"],
                "Destination changed; preview again",
                "conflict",
            )

    def _operation_path(self, identifier):
        require(
            isinstance(identifier, str) and re.fullmatch(r"[0-9a-f]{64}", identifier),
            "Invalid project operation ID",
        )
        return self.home / "operations" / (identifier + ".json")

    def _write_operation(self, operation):
        self.manager._store.put_json(self._operation_path(operation["id"]), operation)

    def _step(self, operation, key, status):
        operation.setdefault("steps", {})[key] = status
        self._write_operation(operation)

    def _work(self, identifier, section, name):
        return self.control / "operations" / identifier / section / name

    def _stage(self, operation):
        for change in operation["plan"]["changes"]:
            if change["kind"] == "adopt":
                self._resolve(change["desired"])
            if change["kind"] not in ("install", "replace"):
                continue
            row = change["desired"]
            target = (
                self._directory(operation["plan"]["after"]["skills_dir"]) / row["name"]
            )
            current = self._observe(target, row["name"])
            previous_identity = operation.setdefault("staged_identities", {}).get(
                row["name"]
            )
            if (
                previous_identity is not None
                and current.get("identity") == previous_identity
            ):
                require(
                    self._matches(current, row),
                    "Published files were modified; preserve edits before recovery",
                    "conflict",
                )
                continue
            path = self._work(operation["id"], "staged", row["name"])
            if os.path.lexists(path) and not self._matches(
                self._observe(path, row["name"]), row
            ):
                retained = self._work(
                    operation["id"], "incomplete-" + secrets.token_hex(8), row["name"]
                )
                fs.make_directories(retained.parent)
                fs.rename_absent(path, retained)
            if not os.path.lexists(path):
                fs.make_directories(path)
                selected = self._resolve(row)
                snapshot = self.manager._managed().backend.snapshot(selected.digest)
                files = {
                    item["path"]: {
                        "content": snapshot["contents"][item["path"]],
                        "executable": item["executable"],
                    }
                    for item in snapshot["manifest"]["files"]
                }
                self.manager._materialize(
                    path, snapshot["manifest"]["directories"], files
                )
            require(
                self._observe(path, row["name"]).get("digest") == row["digest"],
                "Staged package differs from selected content",
                "integrity",
            )
            operation["staged_identities"][row["name"]] = self._observe(
                path, row["name"]
            )["identity"]
        self._write_operation(operation)

    def _matches(self, observed, row):
        return (
            row is not None
            and observed.get("valid")
            and observed.get("digest") == row["digest"]
        )

    def _resume(self, operation):
        plan = operation["plan"]
        state = self._state()
        require(
            (state["revision"] if state else None)
            in (plan["before"], plan["after"]["revision"]),
            "Project advanced after interruption; manual review required",
            "conflict",
        )
        require(
            self._read(self.manifest)
            in (plan["manifest_before"], self._portable(plan["after"]))
            and self._read(self.owner_path) in (plan["owner_before"], self._owner()),
            "Control files changed during recovery",
            "conflict",
        )
        self._stage(operation)
        directory = self._directory(plan["after"]["skills_dir"])
        fs.make_directories(directory)
        for change in plan["changes"]:
            old, desired = change["old"], change["desired"]
            row = old or desired
            target = directory / row["name"]
            backup = self._work(operation["id"], "previous", row["name"])
            observed = self._observe(target, row["name"])
            if change["kind"] in ("adopt", "forget"):
                require(
                    observed == change["observed"],
                    "Destination changed during recovery",
                    "conflict",
                )
                self._step(
                    operation,
                    change["key"],
                    "adopted" if change["kind"] == "adopt" else "forgotten",
                )
                continue
            if old is not None:
                if not os.path.lexists(backup):
                    require(
                        observed == change["observed"],
                        "Project files changed before replacement",
                        "conflict",
                    )
                    fs.make_directories(backup.parent)
                    fs.rename_absent(target, backup)
                # Validate the moved object again before publishing new bytes.
                require(
                    self._observe(backup, row["name"]) == change["observed"],
                    "Moved content changed; retained backup needs review/rollback",
                    "conflict",
                )
                self._step(operation, change["key"], "backed_up")
                observed = self._observe(target, row["name"])
            if change["kind"] == "remove":
                require(
                    not observed["exists"],
                    "Another directory appeared at removed destination",
                    "conflict",
                )
                self._step(operation, change["key"], "removed")
                continue
            if observed["exists"]:
                require(
                    self._matches(observed, desired)
                    and observed.get("identity")
                    == operation["staged_identities"].get(desired["name"]),
                    "Competing destination was preserved",
                    "conflict",
                )
            else:
                stage = self._work(operation["id"], "staged", desired["name"])
                fs.rename_absent(stage, target)
            require(
                self._matches(self._observe(target, desired["name"]), desired),
                "Published package changed",
                "conflict",
            )
            self._step(operation, change["key"], "installed")
        fs.make_directories(self.control)
        fs.write_atomic(self.manifest, canonical(self._portable(plan["after"])) + b"\n")
        fs.write_atomic(self.owner_path, canonical(self._owner()) + b"\n")
        self.manager._store.put_json(self.home / "state.json", plan["after"])
        operation.update(
            status="completed", error=None, revision=plan["after"]["revision"]
        )
        self._write_operation(operation)
        fs.write_atomic(self.pending_path, b"null\n")
        return deepcopy(operation)

    def apply(self, plan):
        plan = self._plan(plan)
        with self._lock():
            identifier = plan["token"][7:]
            previous = self._read(self._operation_path(identifier))
            if previous:
                require(
                    previous["plan"] == plan, "Operation identity mismatch", "integrity"
                )
                require(
                    previous["status"] == "completed",
                    "Interrupted operation requires explicit recovery",
                    "conflict",
                )
                return previous
            self._guard(plan)
            operation = {
                "schema": "skill-manager/project-operation-1",
                "id": identifier,
                "plan": plan,
                "status": "pending",
                "error": None,
            }
            self._write_operation(operation)
            fs.write_atomic(
                self.pending_path,
                canonical({"store_id": self.manager.store_id, "id": identifier})
                + b"\n",
            )
            return self._execute(operation)

    def _execute(self, operation):
        try:
            return self._resume(operation)
        except (SkillError, OSError) as error:
            operation["error"] = str(error)
            try:
                self._write_operation(operation)
            except (SkillError, OSError):
                # The pre-mutation journal remains authoritative if storage fails.
                pass
            raise SkillError(
                "uncertain",
                f"Project operation interrupted: {error}",
                uncertain=True,
                operation_id=operation["id"],
                recovery="Inspect operations and explicitly resume or rollback",
            ) from error

    def operations(self):
        paths = sorted(
            (self.home / "operations").glob("*.json"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        return [json_read(path) for path in paths[:100]]

    def recover(self, identifier, *, action="resume"):
        require(
            action in ("resume", "rollback"),
            "Recovery action must be resume or rollback",
        )
        with self._lock():
            operation = self._read(self._operation_path(identifier))
            require(operation is not None, "Project operation not found", "not_found")
            self._plan(operation["plan"])
            require(
                operation["id"] == identifier == operation["plan"]["token"][7:],
                "Operation identity mismatch",
                "integrity",
            )
            if operation["status"] in ("completed", "rolled_back"):
                if self._read(self.pending_path) == {
                    "store_id": self.manager.store_id,
                    "id": identifier,
                }:
                    fs.write_atomic(self.pending_path, b"null\n")
                return operation
            require(
                self._pending() in (None, identifier),
                "Recover the other pending operation first",
                "conflict",
            )
            fs.write_atomic(
                self.pending_path,
                canonical({"store_id": self.manager.store_id, "id": identifier})
                + b"\n",
            )
            if action == "resume":
                return self._execute(operation)
            plan = operation["plan"]
            state = self._state()
            require(
                (state["revision"] if state else None) == plan["before"]
                and self._read(self.manifest) == plan["manifest_before"]
                and self._read(self.owner_path) == plan["owner_before"],
                "Metadata was published; resume this operation instead of rolling back",
                "conflict",
            )
            directory = self._directory(plan["after"]["skills_dir"])
            for change in reversed(plan["changes"]):
                if change["kind"] in ("adopt", "forget"):
                    continue
                row = change["old"] or change["desired"]
                target = directory / row["name"]
                backup = self._work(identifier, "previous", row["name"])
                observed = self._observe(target, row["name"])
                if os.path.lexists(backup):
                    require(
                        self._observe(backup, row["name"]).get("identity")
                        == change["observed"]["identity"],
                        "Backup identity changed",
                        "conflict",
                    )
                    if observed["exists"]:
                        require(
                            self._matches(observed, change["desired"])
                            and observed.get("identity")
                            == operation.get("staged_identities", {}).get(row["name"]),
                            "New destination edits were preserved; manual review required",
                            "conflict",
                        )
                        retained = self._work(identifier, "rolled-back", row["name"])
                        fs.make_directories(retained.parent)
                        fs.rename_absent(target, retained)
                    fs.rename_absent(backup, target)
                elif change["kind"] == "install" and observed["exists"]:
                    require(
                        self._matches(observed, change["desired"])
                        and observed.get("identity")
                        == operation.get("staged_identities", {}).get(row["name"]),
                        "New destination edits were preserved",
                        "conflict",
                    )
                    retained = self._work(identifier, "rolled-back", row["name"])
                    fs.make_directories(retained.parent)
                    fs.rename_absent(target, retained)
            operation.update(status="rolled_back", error=None)
            self._write_operation(operation)
            fs.write_atomic(self.pending_path, b"null\n")
            return operation

    def connections(self):
        state = self._state()
        result = []
        if state:
            for key, row in state["connections"].items():
                observed = self._observe(
                    self._directory(state["skills_dir"]) / row["name"], row["name"]
                )
                status = (
                    "missing"
                    if not observed["exists"]
                    else "installed"
                    if self._matches(observed, row)
                    else "modified"
                )
                item = {
                    **row,
                    "id": key,
                    "status": status,
                    "destination": str(
                        self._directory(state["skills_dir"]) / row["name"]
                    ),
                }
                item["observed"] = observed
                try:
                    current = self.manager.skill(
                        row["publisher_id"], row["skill_id"]
                    ).resolve()
                    item.update(
                        update_available=current.digest != row["digest"],
                        reconciliation_required=current.status[
                            "reconciliation_required"
                        ],
                    )
                except (SkillError, OSError) as error:
                    item["library_error"] = str(error)
                result.append(item)
        return {
            "schema": "skill-manager/project-connections-1",
            "project": str(self.root),
            "revision": state["revision"] if state else None,
            "skills_dir": state["skills_dir"] if state else None,
            "manifest_modified": self._read(self.manifest)
            != (self._portable(state) if state else None),
            "owner_mismatch": self._read(self.owner_path) not in (None, self._owner()),
            "pending_operation": self._pending(),
            "connections": result,
            "operations": self.operations(),
        }

    def save_variant(
        self, selection, variant_id, *, expected_revision, expected_digest
    ):
        source_id(variant_id)
        with self._lock():
            state = self._state()
            require(
                state and state["revision"] == expected_revision,
                "Stale project receipt",
                "conflict",
            )
            require(
                self._read(self.owner_path) == self._owner(),
                "Project belongs to another store",
                "conflict",
            )
            key = self._keys(state, [selection])[0]
            row = state["connections"][key]
            path = self._directory(state["skills_dir"]) / row["name"]
            snapshot = self.manager._managed().backend.capture(path)
            require(
                snapshot["digest"] == expected_digest,
                "Project edits changed during capture",
                "conflict",
            )
            engine = self.manager._managed()
            require(
                engine.load("local", variant_id, missing_ok=True) is None,
                "Variant already exists",
                "conflict",
            )
            engine.backend.store(snapshot)
            baseline = {"digest": snapshot["digest"], "revision": None, "version": None}
            return engine.commit(
                {
                    "schema": engine.backend.schema,
                    "publisher_id": "local",
                    "id": variant_id,
                    "active_digest": snapshot["digest"],
                    "baseline": baseline,
                    "upstream": baseline,
                    "tracking": False,
                    "sync_error": None,
                    "label": row["name"],
                },
                None,
            )
