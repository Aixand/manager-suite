"""Provider-independent logical prompt targets."""

from pathlib import Path

from ..codec import clone
from ..errors import require
from ..prompts.definitions import DIGEST, fields, name, release_ref


def target(value, *, resolve_paths=True):
    require(isinstance(value, dict), "Prompt target must be an object")
    value = clone(value)
    if value.get("kind") == "managed":
        keys = {"kind", "publisher_id", "package_id", "prompt", "revision"}
        fields(value, keys, keys - {"revision"}, "managed target")
        for key in ("publisher_id", "package_id", "prompt"):
            name(value[key], key)
        if "revision" in value:
            require(
                isinstance(value["revision"], str) and DIGEST.fullmatch(value["revision"]),
                "Managed target revision must be a digest",
            )
        return value
    if value.get("kind") == "release":
        fields(value, {"kind", "reference"}, {"kind", "reference"}, "release target")
        reference = value["reference"]
        require(isinstance(reference, str), "Release reference must be text")
        pack, separator, prompt = reference.partition("/")
        require(separator and "/" not in prompt, "Release target requires pack@version/prompt")
        release_ref(pack)
        name(prompt, "prompt")
        return clone(value)
    require(value.get("kind") == "draft", "Target kind must be release or draft")
    keys = {"kind", "source_id", "pack_path", "prompt"}
    fields(value, keys, keys, "draft target")
    name(value["source_id"], "source identifier")
    name(value["prompt"], "prompt")
    path = value["pack_path"]
    require(
        isinstance(path, str) and "\0" not in path and Path(path).is_absolute(),
        "Pack path must be absolute and contain no NUL characters",
    )
    if resolve_paths:
        path = str(Path(path).resolve())
    else:
        require(
            str(Path(path)) == path and ".." not in Path(path).parts,
            "Stored pack path must be canonical",
        )
    return {**value, "pack_path": path}
