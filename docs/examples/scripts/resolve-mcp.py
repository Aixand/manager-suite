"""Resolve an explicit MCP definition without starting its server."""
import json
import sys
import tempfile
from pathlib import Path

from mcp_manager import Manager


def main():
    with tempfile.TemporaryDirectory(prefix="manager-mcp-") as temporary:
        root = Path(temporary)
        server = root / "server.py"
        server.write_text("raise RuntimeError('Resolution must not execute this file')\n")
        manager = Manager(home=root / "mcp")
        created = manager.add("docs", {"command": sys.executable, "args": [str(server)]})
        updated = manager.configure("docs", {"tool_timeout_sec": 90},
                                    expected_revision=created["revision"])
        resolved = manager.resolve(["docs"], str(root), project=None, check=False)
        assert resolved["servers"]["docs"]["tool_timeout_sec"] == 90
        assert updated["revision"] != created["revision"]
        print(json.dumps({"selected": list(resolved["servers"]), "tool_timeout_sec": 90,
                          "server_started": False}))


if __name__ == "__main__":
    main()
