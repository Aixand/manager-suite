"""Pack and captured-snapshot validation without filesystem access."""

from ..codec import digest
from ..errors import require
from .composition import resolve_asset
from .definitions import (
    DIGEST,
    RENDERER,
    ROLES,
    SNAPSHOT_SCHEMA,
    asset_path,
    fields,
    name,
    release_ref,
)
from .inputs import bind, check_value, input_specs
from .templates import check_template


def check_manifest(pack):
    fields(
        pack,
        {
            "schema",
            "name",
            "description",
            "dependencies",
            "sections",
            "documents",
            "prompts",
            "examples",
        },
        {"schema", "name", "prompts"},
        "pack",
    )
    require(pack["schema"] == "prompt-manager/pack-1", "Unsupported pack schema")
    name(pack["name"], "pack.name")
    if "description" in pack:
        require(isinstance(pack["description"], str), "pack.description must be text")
    for kind in ("dependencies", "sections", "documents", "prompts"):
        require(isinstance(pack.get(kind, {}), dict), f"pack.{kind} must be an object")
        for key in pack.get(kind, {}):
            name(key, f"pack.{kind}")
    for alias, ref in pack.get("dependencies", {}).items():
        release_ref(ref)
    for kind in ("sections", "documents"):
        for key, asset in pack.get(kind, {}).items():
            fields(
                asset,
                {"file", "inputs"} if kind == "sections" else {"file"},
                {"file"},
                f"{kind}.{key}",
            )
            require(isinstance(asset["file"], str) and asset["file"], f"{kind}.{key}: invalid file")
            if kind == "sections":
                input_specs(asset.get("inputs", {}), f"sections.{key}.inputs")
    for key, prompt in pack["prompts"].items():
        fields(prompt, {"description", "inputs", "sections"}, {"sections"}, f"prompts.{key}")
        if "description" in prompt:
            require(isinstance(prompt["description"], str), f"{key}: invalid description")
        input_specs(prompt.get("inputs", {}), f"prompts.{key}.inputs")
        require(
            isinstance(prompt["sections"], list) and prompt["sections"],
            f"{key}: sections must be a nonempty list",
        )
        ids = set()
        for entry in prompt["sections"]:
            fields(
                entry,
                {"id", "section", "document", "inputs", "when", "heading", "role"},
                {"id"},
                f"prompts.{key}.sections",
            )
            name(entry["id"], f"prompts.{key}.sections.id")
            require(entry["id"] not in ids, f"{key}: duplicate section id {entry['id']}")
            ids.add(entry["id"])
            require(
                ("section" in entry) != ("document" in entry), f"{key}: select section or document"
            )
            require(
                isinstance(entry.get("role", "user"), str) and entry.get("role", "user") in ROLES,
                f"{key}: unsupported message role",
            )
            if "heading" in entry:
                require(
                    isinstance(entry["heading"], str)
                    and "\n" not in entry["heading"]
                    and "\r" not in entry["heading"],
                    f"{key}: heading must be one line",
                )
            if "when" in entry:
                condition = entry["when"]
                fields(condition, {"input", "equals"}, {"input", "equals"}, f"{key}.when")
                require(
                    isinstance(condition["input"], str)
                    and condition["input"] in prompt.get("inputs", {}),
                    f"{key}: condition refers to undeclared input",
                )
                spec = prompt["inputs"][condition["input"]]
                require(
                    spec["type"] in {"string", "number", "integer", "boolean", "null"},
                    f"{key}: condition must compare scalar values",
                )
                check_value(condition["equals"], spec, f"{key}.when.equals")
    examples = pack.get("examples", [])
    require(isinstance(examples, list), "pack.examples must be a list")
    ids = set()
    for example in examples:
        fields(
            example,
            {"name", "prompt", "inputs", "expect"},
            {"name", "prompt", "inputs", "expect"},
            "example",
        )
        name(example["name"], "example.name")
        require(example["name"] not in ids, "Duplicate example name")
        ids.add(example["name"])
        require(
            isinstance(example["prompt"], str) and example["prompt"] in pack["prompts"],
            "Example references an unknown prompt",
        )
        require(isinstance(example["inputs"], dict), "Example inputs must be an object")
        expect = example["expect"]
        fields(
            expect,
            {"contains", "excludes", "section_ids", "text", "messages"},
            set(),
            "example.expect",
        )
        require(bool(expect), "Example must declare at least one expectation")
        for field in ("contains", "excludes", "section_ids"):
            if field in expect:
                require(
                    isinstance(expect[field], list)
                    and all(isinstance(x, str) for x in expect[field]),
                    f"example.expect.{field} must be a list of strings",
                )
        if "text" in expect:
            require(isinstance(expect["text"], str), "example.expect.text must be text")
        if "messages" in expect:
            require(isinstance(expect["messages"], list), "example.expect.messages must be a list")


def validate_mapping(entry, prompt_specs, section_specs, where):
    mapping = entry.get("inputs", {})
    require(isinstance(mapping, dict), f"{where}: inputs must be a mapping")
    require(not (set(mapping) - set(section_specs)), f"{where}: undeclared section inputs")
    for key, spec in section_specs.items():
        require(key in mapping or "default" in spec, f"{where}: missing mapping for {key}")
    for key, source in mapping.items():
        fields(source, {"input", "value"}, set(), f"{where}.{key}")
        require(len(source) == 1, f"{where}.{key}: choose input or value")
        if "value" in source:
            check_value(source["value"], section_specs[key], f"{where}.{key}")
        else:
            require(
                isinstance(source["input"], str) and source["input"] in prompt_specs,
                f"{where}.{key}: unknown prompt input",
            )
            origin = prompt_specs[source["input"]]
            target = section_specs[key]
            require(
                origin["type"] == target["type"]
                or (origin["type"] == "integer" and target["type"] == "number"),
                f"{where}.{key}: incompatible input types",
            )


def validate_snapshot(snapshot, ancestors=()):
    require(len(ancestors) < 32, "Dependency nesting exceeds 32 packs")
    fields(
        snapshot,
        {"schema", "renderer", "pack", "files", "dependencies"},
        {"schema", "renderer", "pack", "files", "dependencies"},
        "snapshot",
    )
    require(snapshot["schema"] == SNAPSHOT_SCHEMA, "Unsupported snapshot schema")
    require(snapshot["renderer"] == RENDERER, "Unsupported renderer version")
    pack = snapshot["pack"]
    check_manifest(pack)
    require(pack["name"] not in ancestors, f"Dependency cycle involving {pack['name']}")
    require(isinstance(snapshot["files"], dict), "Snapshot files must be an object")
    require(isinstance(snapshot["dependencies"], dict), "Snapshot dependencies must be an object")
    require(
        set(snapshot["dependencies"]) == set(pack.get("dependencies", {})),
        "Snapshot dependencies do not match manifest",
    )
    for alias, child in snapshot["dependencies"].items():
        fields(child, {"digest", "snapshot"}, {"digest", "snapshot"}, f"dependency {alias}")
        require(
            isinstance(child["digest"], str) and DIGEST.fullmatch(child["digest"]),
            f"Invalid dependency digest: {alias}",
        )
        require(
            digest(child["snapshot"]) == child["digest"],
            f"Dependency digest mismatch: {alias}",
            "integrity",
        )
        validate_snapshot(child["snapshot"], (*ancestors, pack["name"]))
        expected_name, version = release_ref(pack["dependencies"][alias])
        require(
            child["snapshot"]["pack"]["name"] == expected_name, f"Dependency name mismatch: {alias}"
        )
        if DIGEST.fullmatch(version):
            require(child["digest"] == version, f"Dependency pin mismatch: {alias}", "integrity")
    expected_files = set()
    for kind in ("sections", "documents"):
        for key, asset in pack.get(kind, {}).items():
            asset_path(asset["file"])
            expected_files.add(asset["file"])
            require(asset["file"] in snapshot["files"], f"Missing snapshot file: {asset['file']}")
            text = snapshot["files"][asset["file"]]
            require(isinstance(text, str), f"Invalid snapshot content: {asset['file']}")
            if kind == "sections":
                check_template(text, asset.get("inputs", {}), f"section {key}")
    require(set(snapshot["files"]) == expected_files, "Unexpected snapshot files")
    for key, prompt in pack["prompts"].items():
        for entry in prompt["sections"]:
            _, kind, _, asset = resolve_asset(snapshot, entry)
            if kind == "documents":
                require(
                    "inputs" not in entry,
                    f"{key}/{entry['id']}: documents are literal; inputs unsupported",
                )
            else:
                validate_mapping(
                    entry, prompt.get("inputs", {}), asset.get("inputs", {}), f"{key}/{entry['id']}"
                )
    for example in pack.get("examples", []):
        bind(
            pack["prompts"][example["prompt"]].get("inputs", {}),
            example["inputs"],
            f"example {example['name']}",
        )
