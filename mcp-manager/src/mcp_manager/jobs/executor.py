"""Detached preparation worker; each request retains its captured definitions."""
from pathlib import Path
import sys
import uuid

from ..storage.records import read
from ..preparation.service import prepare_tool
from .service import update


def worker(root, identifier):
    request = read(Path(root) / "jobs" / identifier / "request.json")
    try:
        completed = {}
        for tool in request["tools"]:
            completed[tool] = prepare_tool(root, identifier, tool, request)
            update(root, identifier, completed=completed)
        update(root, identifier, status="completed", step="completed")
    except Exception as error:
        update(root, identifier, status="failed", error=str(error))



def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    worker(Path(argv[0]), str(uuid.UUID(argv[1])))


if __name__ == "__main__":
    main()
