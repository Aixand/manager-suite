"""Build a conditional pack, check exact messages, and render a source-free release."""
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from prompt_manager import Manager


def main():
    with TemporaryDirectory(prefix="prompt-compose-") as temporary:
        root = Path(temporary)
        source = root / "pack"
        source.mkdir()
        (source / "task.md").write_text("Review {{ task }}.", encoding="utf-8")
        (source / "guide.md").write_text("Literal {{ example }}.", encoding="utf-8")
        manifest = {
            "schema": "prompt-manager/pack-1", "name": "conditional",
            "sections": {"task": {"file": "task.md", "inputs": {"task": {"type": "string"}}}},
            "documents": {"guide": {"file": "guide.md"}},
            "prompts": {"review": {
                "inputs": {"task": {"type": "string"}, "guide": {"type": "boolean", "default": True}},
                "sections": [
                    {"id": "task", "section": "task", "role": "system", "inputs": {"task": {"input": "task"}}},
                    {"id": "guide", "document": "guide", "when": {"input": "guide", "equals": True}},
                ],
            }},
            "examples": [{"name": "without-guide", "prompt": "review",
                          "inputs": {"task": "parser", "guide": False},
                          "expect": {"messages": [{"role": "system", "content": "Review parser."}],
                                     "section_ids": ["task"]}}],
        }
        (source / "pack.json").write_text(json.dumps(manifest), encoding="utf-8")
        manager = Manager(home=root / "store", packs=[])
        validated = manager.validate(source)
        assert validated["examples_passed"] == ["without-guide"]
        rendered = manager.render(source, {"task": "{{ untouched }}"}, prompt="review", format="messages")
        assert rendered["output"] == [
            {"role": "system", "content": "Review {{ untouched }}."},
            {"role": "user", "content": "Literal {{ example }}."},
        ]
        manager.release(source, "v1")
        shutil.rmtree(source)
        pinned = manager.render("conditional@v1/review", {"task": "parser", "guide": False}, format="messages")
        assert pinned["output"] == [{"role": "system", "content": "Review parser."}]
        assert [row["id"] for row in pinned["sections"] if row["included"]] == ["task"]
        assert manager.check_store()["ok"]
        print("PASS: conditions, literal documents, single-pass substitution, and source-free release")


if __name__ == "__main__":
    main()
