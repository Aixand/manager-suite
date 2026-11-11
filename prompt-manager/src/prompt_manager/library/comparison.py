"""Compare captured definitions and rendered content."""

import difflib
import json

from ..codec import clone
from ..prompts.rendering import render
from .snapshots import load, select


def _diff(before, after, left, right):
    def lines(value):
        if isinstance(value, str):
            return value.splitlines(keepends=True)
        return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").splitlines(
            keepends=True
        )

    return "".join(difflib.unified_diff(lines(before), lines(after), fromfile=left, tofile=right))


def compare(store, packs, before, after, *, inputs=None, prompt=None, format="text"):
    left, left_id, left_prompt = load(store, packs, before, prompt)
    right, right_id, right_prompt = load(store, packs, after, prompt)
    return compare_snapshots(
        left,
        right,
        left_id,
        right_id,
        inputs=inputs,
        left_prompt=left_prompt,
        right_prompt=right_prompt,
        format=format,
    )


def compare_snapshots(
    left,
    right,
    left_id,
    right_id,
    *,
    inputs=None,
    left_prompt=None,
    right_prompt=None,
    format="text",
):
    definition = _diff(left, right, "before/snapshot.json", "after/snapshot.json")
    rendered = None
    if inputs is not None:
        left_prompt = select(left, left_prompt)
        right_prompt = select(right, right_prompt)
        supplied = clone(inputs)
        a = render(left, left_prompt, supplied, left_id, format)
        b = render(right, right_prompt, supplied, right_id, format)
        rendered = _diff(a["output"], b["output"], "before/rendered", "after/rendered")
    return {
        "changed": left_id["digest"] != right_id["digest"] or bool(rendered),
        "before": left_id,
        "after": right_id,
        "definition_diff": definition,
        "render_diff": rendered,
    }
