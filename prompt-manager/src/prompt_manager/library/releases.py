"""Immutable prompt release labels and snapshot resolution."""

from pathlib import Path

from ..codec import digest
from ..errors import PromptError, require
from ..prompts.definitions import DIGEST, VERSION, fields, name, release_ref
from ..prompts.examples import check_examples
from ..prompts.validation import validate_snapshot
from ..storage.files import read_json
from ..storage.locks import lock
from ..storage.objects import publish_immutable
from ..storage.paths import default_home
from .discovery import source as catalog_source
from .snapshots import load, read_draft


class Store:
    def __init__(self, home=None):
        self.home = (Path(home) if home is not None else default_home()).expanduser().resolve()

    def load(self, reference):
        pack, version = release_ref(reference)
        if DIGEST.fullmatch(version):
            identity = {"name": pack, "version": None, "digest": version}
        else:
            path = self.home / "releases" / pack / f"{version}.json"
            require(path.exists(), f"Release not found: {reference}", "not_found")
            identity = read_json(path)
            fields(
                identity, {"name", "version", "digest"}, {"name", "version", "digest"}, "release"
            )
            require(
                identity["name"] == pack and identity["version"] == version,
                "Release identity mismatch",
                "integrity",
            )
            require(
                isinstance(identity["digest"], str) and DIGEST.fullmatch(identity["digest"]),
                "Invalid release digest",
                "integrity",
            )
        path = self.home / "objects" / f"{identity['digest'][7:]}.json"
        require(path.exists(), f"Missing release object: {identity['digest']}", "not_found")
        snapshot = read_json(path)
        require(
            digest(snapshot) == identity["digest"], "Release content digest mismatch", "integrity"
        )
        validate_snapshot(snapshot)
        require(snapshot["pack"]["name"] == pack, "Release pack name mismatch", "integrity")
        return snapshot, identity

    def publish(self, snapshot, version):
        name(version, "release version", VERSION)
        identity = {
            "name": snapshot["pack"]["name"],
            "version": version,
            "digest": digest(snapshot),
        }
        try:
            publish_immutable(self.home / "objects" / f"{identity['digest'][7:]}.json", snapshot)
            publish_immutable(
                self.home / "releases" / identity["name"] / f"{version}.json", identity
            )
        except OSError as exc:
            raise PromptError("io", f"Cannot publish release: {exc}") from exc
        return identity

    def list(self):
        releases = []
        for path in sorted((self.home / "releases").glob("*/*.json")):
            _, identity = self.load(f"{path.parent.name}@{path.stem}")
            releases.append(identity)
        return releases


def release(store, packs, source, version):
    snapshot, _, selected = load(store, packs, source)
    require(selected is None, "Release a whole pack, not a selected prompt")
    check_examples(snapshot)
    return store.publish(snapshot, version)


def release_catalog(store, packs, registry, identifier, version, *, expected_digest):
    row, source = catalog_source(store, packs, registry, identifier)
    require(row["kind"] == "draft", "Select a draft to publish")
    with lock(store.home, str(source)):
        snapshot = read_draft(source, store)
        require(
            digest(snapshot) == expected_digest,
            "Draft changed since it was opened; refresh before publishing",
            "conflict",
        )
        check_examples(snapshot)
        return store.publish(snapshot, version)
