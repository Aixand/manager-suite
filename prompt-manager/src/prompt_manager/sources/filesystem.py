"""Filesystem capture and local draft discovery."""

from ..codec import MAX_BYTES
from ..errors import PromptError, require
from ..prompts.definitions import asset_path
from ..prompts.validation import check_manifest
from ..storage.files import read_json


def capture_files(root, pack):
    files = {}
    for kind in ("sections", "documents"):
        for asset in pack.get(kind, {}).values():
            relative = asset_path(asset["file"])
            source = (root / relative).resolve()
            require(source.is_relative_to(root), f"Asset leaves pack directory: {relative}")
            try:
                with source.open("rb") as stream:
                    raw = stream.read(MAX_BYTES + 1)
                require(len(raw) <= MAX_BYTES, f"Asset too large: {relative}", "size")
                files[str(relative)] = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            except (OSError, UnicodeError) as exc:
                raise PromptError("io", f"Cannot read asset {source}: {exc}") from exc
    return files


def discover(packs):
    found = {}
    for root in packs:
        paths = (
            [root / "pack.json"]
            if (root / "pack.json").is_file()
            else sorted(root.glob("*/pack.json"))
        )
        for path in paths:
            pack = read_json(path)
            check_manifest(pack)
            key = pack["name"]
            directory = path.parent.resolve()
            require(
                key not in found or found[key][0] == directory,
                f"Ambiguous draft pack {key}; use an explicit path",
                "ambiguous",
            )
            found[key] = directory, pack
    return found
