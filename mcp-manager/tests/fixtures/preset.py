"""Local Sonar/Lore CLI and stdio fixture; never installs or calls a model."""
import json
from pathlib import Path
import sqlite3
import sys
import time

args = sys.argv[1:]


def argument(flag, default=None):
    return args[args.index(flag) + 1] if flag in args else default


control_path = Path(argument("--fixture-control"))
control = json.loads(control_path.read_text()) if control_path.exists() else {}
log = Path(argument("--fixture-log"))
with log.open("a") as stream:
    stream.write(json.dumps(args) + "\n")
database = Path(argument("--db")) if "--db" in args else Path(argument("--index-dir")) / "index.sqlite"

if "mcp" in args:
    for line in sys.stdin:
        message = json.loads(line)
        if "id" not in message:
            continue
        if control.get("invalid_message"):
            print("[]", flush=True)
            sys.exit(0)
        if message["method"] == "initialize":
            result = {"protocolVersion": message["params"]["protocolVersion"],
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "fixture", "version": "1"}}
        else:
            if control.get("disconnect"):
                sys.exit(0)
            if control.get("timeout"):
                time.sleep(2)
            with sqlite3.connect(database) as connection:
                root = connection.execute("SELECT value FROM metadata WHERE key='root'").fetchone()[0]
            health = {"root": control.get("health_root", root),
                      "index": {"syntax_freshness": control.get("freshness", "current")},
                      "needs_reindex": control.get("stale", False)}
            result = {"content": [{"type": "text", "text": json.dumps(health)}]}
            if control.get("invalid_health"):
                result = {"content": [{"type": "text", "text": "[]"}]}
            if not control.get("text_only") and not control.get("invalid_health"):
                result["structuredContent"] = health
            if control.get("health_error"):
                result["isError"] = True
            if control.get("invalid_result"):
                result = []
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
else:
    if any(command in args for command in control.get("fail_commands", [])):
        sys.exit(7)
    if "index" in args:
        time.sleep(control.get("index_delay", 0))
        project = str(Path(args[args.index("index") + 1]).resolve())
        database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT)")
            connection.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", ("root", control.get("index_root", project)))
        print(json.dumps({"root": project}))
    else:
        print("{}")
