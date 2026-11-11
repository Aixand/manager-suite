"""Ordered section resolution and message composition without I/O."""

from ..codec import canonical, digest
from ..errors import require
from .inputs import bind
from .templates import render_template


def resolve_asset(snapshot, entry):
    kind = "sections" if "section" in entry else "documents"
    reference = entry["section" if kind == "sections" else "document"]
    require(isinstance(reference, str), "Asset reference must be text")
    parts = reference.split("/")
    require(len(parts) in {1, 2} and all(parts), f"Invalid asset reference: {reference}")
    owner = snapshot
    if len(parts) == 2:
        require(parts[0] in snapshot["dependencies"], f"Unknown dependency alias: {parts[0]}")
        owner = snapshot["dependencies"][parts[0]]["snapshot"]
    key = parts[-1]
    require(key in owner["pack"].get(kind, {}), f"Unknown {kind} asset: {reference}")
    return owner, kind, key, owner["pack"][kind][key]


def compose(snapshot, prompt_name, values):
    prompt = snapshot["pack"]["prompts"][prompt_name]
    messages, trace = [], []
    for entry in prompt["sections"]:
        owner, kind, key, asset = resolve_asset(snapshot, entry)
        condition = entry.get("when")
        included = condition is None or canonical(values[condition["input"]]) == canonical(
            condition["equals"]
        )
        role = entry.get("role", "user")
        trace.append(
            {
                "id": entry["id"],
                "kind": kind[:-1],
                "asset": f"{owner['pack']['name']}/{key}",
                "pack_digest": digest(owner),
                "included": included,
                "role": role,
            }
        )
        if not included:
            continue
        body = owner["files"][asset["file"]]
        if kind == "sections":
            supplied = {
                name: values[source["input"]] if "input" in source else source["value"]
                for name, source in entry.get("inputs", {}).items()
            }
            section_values = bind(asset.get("inputs", {}), supplied, f"{prompt_name}/{entry['id']}")
            body = render_template(body, section_values)
        body = body.rstrip("\n")
        if "heading" in entry:
            body = f"## {entry['heading']}\n\n{body}"
        messages.append({"role": role, "content": body})
    return messages, trace
