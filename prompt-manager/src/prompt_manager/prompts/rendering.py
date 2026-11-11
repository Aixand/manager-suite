"""Deterministic prompt rendering without I/O or input persistence."""

from ..codec import digest
from ..errors import require
from .composition import compose
from .definitions import RENDERER
from .inputs import bind


def render(snapshot, prompt_name, inputs, identity, output_format="text"):
    require(output_format in {"text", "messages"}, "Format must be text or messages")
    prompts = snapshot["pack"]["prompts"]
    require(prompt_name in prompts, f"Unknown prompt: {prompt_name}", "not_found")
    prompt = prompts[prompt_name]
    values = bind(prompt.get("inputs", {}), inputs, prompt_name)
    messages, trace = compose(snapshot, prompt_name, values)
    text = "\n\n".join(f"[{message['role']}]\n{message['content']}" for message in messages)
    if messages:
        text += "\n"
    output = text if output_format == "text" else messages
    return {
        "schema": "prompt-manager/render-result-1",
        "renderer": RENDERER,
        "prompt": f"{snapshot['pack']['name']}/{prompt_name}",
        "release": dict(identity),
        "format": output_format,
        "output": output,
        "input_digest": digest(values),
        "output_digest": digest(output),
        "sections": trace,
        "dependencies": {
            alias: {"name": child["snapshot"]["pack"]["name"], "digest": child["digest"]}
            for alias, child in snapshot["dependencies"].items()
        },
    }
