"""Shared GitHub acquisition with retained MCP source identity compatibility.

MCP owns pinning, package-directory selection and its historical descriptor/tree
encodings. The utility owns network transport, reference resolution and comparison.
"""

from copy import deepcopy
from pathlib import PurePosixPath

from github_source import Client, GitHubError
from github_source.urls import COMMIT, parse_url as shared_parse_url

from .records import SourceError, digest, require, validate_tree


def parse_url(url):
    try:
        return shared_parse_url(url)
    except GitHubError as error:
        raise SourceError(error.code, str(error)) from error


class GitHub:
    def __init__(self, client=None):
        self.client = client if client is not None else Client()
        # Accept injected source clients using either relation or compare.
        # Shared clients implement the fetch/compare contract.
        self.legacy = not callable(getattr(self.client, "compare", None)) and callable(
            getattr(self.client, "relation", None)
        )

    def resolve(self, url, *, ref=None, path=None, track=None):
        try:
            if self.legacy:
                return self.client.resolve(url, ref=ref, path=path, track=track)
            source = self.client.resolve(url, ref=ref, path=path)
            require(track is None or type(track) is bool, "Tracking must be boolean")
            tracking = source["reference_kind"] == "branch" if track is None else track
            require(
                not (tracking and COMMIT.fullmatch(source["ref"])),
                "A commit is pinned; track a branch or tag instead",
            )
            return {
                key: value
                for key, value in source.items()
                if key not in ("schema", "reference_kind")
            } | {"schema": "resource-sources/github-1", "pinned": not tracking}
        except GitHubError as error:
            raise SourceError(error.code, str(error)) from error

    @staticmethod
    def resolved(source):
        keys = (
            "kind",
            "repository_id",
            "repository",
            "url",
            "ref",
            "path",
            "commit",
            "tree_sha",
        )
        # Historical records did not retain reference kind. It is descriptive
        # during immutable fetch/compare; pinning policy stays in the MCP record.
        kind = (
            "commit"
            if COMMIT.fullmatch(source["ref"])
            else "tag"
            if source.get("pinned")
            else "branch"
        )
        return {key: source[key] for key in keys if key in source} | {
            "schema": "github-source/source-1",
            "reference_kind": kind,
        }

    def snapshot(self, source):
        try:
            if self.legacy:
                tree, _ = self.client.snapshot(source)
            else:
                selected = self.resolved(source)
                path = PurePosixPath(selected["path"])
                # A package metadata file selects its containing source package,
                # keeping sibling executables/lockfiles available for installation.
                if path.name in ("server.json", "package.json", "pyproject.toml"):
                    selected["path"] = (
                        "" if str(path.parent) == "." else str(path.parent)
                    )
                snapshot = self.client.fetch(selected)
                tree = {**snapshot["tree"], "schema": "resource-sources/tree-1"}
            validate_tree(tree)
            return tree, digest(tree)
        except GitHubError as error:
            raise SourceError(error.code, str(error)) from error

    def relation(self, source, commit):
        if source["commit"] == commit:
            return "identical"
        try:
            if self.legacy:
                return self.client.relation(source, commit)
            before = self.resolved(source)
            after = deepcopy(before)
            after["commit"] = commit
            return self.client.compare(before, after)["relationship"]
        except GitHubError as error:
            raise SourceError(error.code, str(error)) from error
