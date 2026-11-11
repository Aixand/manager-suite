"""Optional GitHub SDK bridge; stored descriptor validation works offline."""

import re
from urllib.parse import unquote, urlsplit

from ._source_records import SourceError, digest, require

COMMIT = re.compile(r"[0-9a-f]{40,64}")
REPO_PART = re.compile(r"[A-Za-z0-9_.-]{1,100}")


def parse_url(url):
    require(isinstance(url, str) and len(url) <= 2048, "Invalid GitHub URL")
    parts = urlsplit(url)
    require(
        parts.scheme == "https"
        and parts.hostname == "github.com"
        and parts.port in (None, 443)
        and not parts.username
        and not parts.password
        and not parts.query,
        "Use an HTTPS github.com repository or file URL",
    )
    path = unquote(parts.path).strip("/").split("/")
    require(
        len(path) >= 2
        and all(REPO_PART.fullmatch(p) and p not in (".", "..") for p in path[:2]),
        "Invalid GitHub repository",
    )
    owner, repo = path[:2]
    repo = repo.removesuffix(".git")
    tail = path[2:]
    require(
        not tail or (tail[0] in ("tree", "blob") and len(tail) >= 2),
        "Use a repository, tree or blob URL",
    )
    return owner, repo, tail[1:] if tail else []


class GitHub:
    def __init__(self):
        try:
            from github_source import Client, GitHubError
        except ModuleNotFoundError as error:
            if error.name != "github_source":
                raise
            raise SourceError(
                "unsupported",
                "GitHub support is optional; install aixand-skill-manager[github]",
            ) from None
        self.client = Client()
        self.error = GitHubError

    def resolve(self, url, *, ref=None, path=None, track=None):
        try:
            resolved = self.client.resolve(url, ref=ref, path=path)
        except self.error as error:
            raise SourceError(error.code, str(error)) from error
        kind = resolved.pop("reference_kind")
        require(track is None or type(track) is bool, "Tracking must be boolean")
        require(
            not (track and kind == "commit"),
            "A commit is pinned; track a branch or tag instead",
        )
        return {
            **resolved,
            "schema": "resource-sources/github-1",
            "pinned": kind != "branch" if track is None else not track,
        }

    def _source(self, source):
        result = {
            key: value
            for key, value in source.items()
            if key
            in {
                "repository_id",
                "repository",
                "url",
                "ref",
                "path",
                "commit",
                "tree_sha",
                "kind",
            }
        }
        result["schema"] = "github-source/source-1"
        result["reference_kind"] = (
            "commit" if COMMIT.fullmatch(result["ref"]) else "branch"
        )
        return result

    def snapshot(self, source):
        resolved = self._source(source)
        # A SKILL.md link selects the complete native package, not just its text.
        if resolved["path"].rsplit("/", 1)[-1] == "SKILL.md":
            resolved["path"] = resolved["path"].rpartition("/")[0]
        try:
            snapshot = self.client.fetch(resolved)
        except self.error as error:
            raise SourceError(error.code, str(error)) from error
        tree = {**snapshot["tree"], "schema": "resource-sources/tree-1"}
        return tree, digest(tree)

    def relation(self, source, commit):
        try:
            before = self._source(source)
            after = {**before, "commit": commit}
            return self.client.compare(before, after)["relationship"]
        except self.error as error:
            raise SourceError(error.code, str(error)) from error
