"""Run after publishing common@1 and demo@1 to the selected home."""

import json
import os

from prompt_manager import Manager

manager = Manager(os.environ.get("PROMPT_MANAGER_HOME"))
result = manager.render(
    "demo@1/review",
    {"task": "Review the pack format.", "context": {"language": "Python"}},
)
print(result["output"], end="")
print(
    json.dumps({"release": result["release"], "output_digest": result["output_digest"]}, indent=2)
)
