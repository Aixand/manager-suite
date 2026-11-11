"""Network-free client implementing the shared GitHub library's public contract."""

import base64
from copy import deepcopy

from github_source.contracts import digest, validate_tree


class GitHubFixture:
    def __init__(self, files):
        self.head = "a" * 40
        self.relation_status = "ahead"
        self.fetches = []
        self.comparisons = []
        self._set_files(files)

    def _set_files(self, files):
        self.tree = validate_tree(
            {
                "schema": "github-source/tree-1",
                "directories": [],
                "files": {
                    path: {
                        "content_b64": base64.b64encode(
                            value if isinstance(value, bytes) else value.encode()
                        ).decode(),
                        "executable": path.endswith(".py"),
                    }
                    for path, value in files.items()
                },
            }
        )

    def resolve(self, url, *, ref=None, path=None):
        ref = ref or "main"
        return {
            "schema": "github-source/source-1",
            "kind": "github",
            "repository_id": "123",
            "repository": "example/resources",
            "url": "https://github.com/example/resources",
            "ref": ref,
            "path": path or "",
            "commit": ref if len(ref) == 40 else self.head,
            "reference_kind": "commit"
            if len(ref) == 40
            else "tag"
            if ref.startswith("v")
            else "branch",
        }

    def fetch(self, source):
        self.fetches.append(deepcopy(source))
        return {
            "schema": "github-source/snapshot-1",
            "source": deepcopy(source),
            "tree": deepcopy(self.tree),
            "digest": digest(self.tree),
        }

    def compare(self, before, after):
        self.comparisons.append((deepcopy(before), deepcopy(after)))
        return {
            "before": before["commit"],
            "after": after["commit"],
            "relationship": "identical"
            if before["commit"] == after["commit"]
            else self.relation_status,
        }

    def change(self, files):
        self._set_files(files)
        self.head = chr(ord(self.head[0]) + 1) * 40
