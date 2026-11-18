"""Create and render an owned prompt in a temporary store; no model call."""
import json
import tempfile
from pathlib import Path

from prompt_manager import Manager


def main():
    with tempfile.TemporaryDirectory(prefix="manager-prompt-") as temporary:
        manager = Manager(home=Path(temporary) / "prompts", packs=[])
        created = manager.create_managed(
            "review", content="Review carefully and check missing tests.", role="system"
        )
        rendered = manager.render_managed("local", "review", "review", format="messages")
        assert rendered["output"] == [
            {"role": "system", "content": "Review carefully and check missing tests."}
        ]
        assert created["id"] == "review"
        print(json.dumps(rendered["output"], indent=2))


if __name__ == "__main__":
    main()
