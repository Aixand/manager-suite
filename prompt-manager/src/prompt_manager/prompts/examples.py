"""Saved rendering expectations used during validation and publication."""

from ..codec import digest
from ..errors import require
from .rendering import render


def check_examples(snapshot):
    identity = {"name": snapshot["pack"]["name"], "version": None, "digest": digest(snapshot)}
    passed = []
    for example in snapshot["pack"].get("examples", []):
        result = render(snapshot, example["prompt"], example["inputs"], identity)
        expect = example["expect"]
        prefix = f"Example {example['name']}"
        for fragment in expect.get("contains", []):
            require(
                fragment in result["output"],
                f"{prefix}: missing expected text {fragment!r}",
                "example",
            )
        for fragment in expect.get("excludes", []):
            require(
                fragment not in result["output"],
                f"{prefix}: unexpected text {fragment!r}",
                "example",
            )
        if "section_ids" in expect:
            actual = [row["id"] for row in result["sections"] if row["included"]]
            require(actual == expect["section_ids"], f"{prefix}: section order differs", "example")
        if "text" in expect:
            require(
                result["output"] == expect["text"], f"{prefix}: rendered text differs", "example"
            )
        if "messages" in expect:
            actual = render(snapshot, example["prompt"], example["inputs"], identity, "messages")[
                "output"
            ]
            require(actual == expect["messages"], f"{prefix}: messages differ", "example")
        passed.append(example["name"])
    return passed
