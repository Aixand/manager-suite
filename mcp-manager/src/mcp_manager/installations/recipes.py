"""Validate explicit MCP installation recipes without executing packages."""

import re
from ..sources.records import relative, require

FIELDS = {
    "schema",
    "kind",
    "package",
    "version",
    "entrypoint",
    "subdirectory",
    "source_digest",
    "reason",
}
KINDS = {"npm", "pypi", "node-source", "python-source", "manual"}


def validate(recipe):
    require(
        isinstance(recipe, dict)
        and not set(recipe) - FIELDS
        and recipe.get("schema") == "mcp-manager/installation-1"
        and recipe.get("kind") in KINDS,
        "Invalid MCP installation recipe",
    )
    for key, value in recipe.items():
        require(
            isinstance(value, str) and len(value) <= 1024 and "\0" not in value,
            "Installation fields must be bounded strings",
        )
    kind = recipe["kind"]
    if kind in ("npm", "pypi"):
        package = recipe.get("package", "")
        pattern = (
            r"(?:@[a-z0-9_.-]+/)?[a-z0-9][a-z0-9_.-]*"
            if kind == "npm"
            else r"[A-Za-z0-9][A-Za-z0-9_.-]*"
        )
        require(re.fullmatch(pattern, package), "Invalid package identifier")
        require(
            re.fullmatch(r"[0-9][A-Za-z0-9.+_-]{0,127}", recipe.get("version", "")),
            "Installation requires an exact package version",
        )
    if kind in ("node-source", "python-source"):
        from ..sources.records import DIGEST

        require(
            DIGEST.fullmatch(recipe.get("source_digest", "")),
            "Source installation needs an owned source snapshot",
        )
        relative(recipe.get("subdirectory", ""), empty=True)
    if "source_digest" in recipe:
        from ..sources.records import DIGEST

        require(
            DIGEST.fullmatch(recipe["source_digest"]),
            "Invalid installation source digest",
        )
    if "entrypoint" in recipe:
        relative(recipe["entrypoint"])
        if kind in ("pypi", "python-source"):
            require(
                "/" not in recipe["entrypoint"],
                "Python entrypoint must be a console script name",
            )
    return recipe
