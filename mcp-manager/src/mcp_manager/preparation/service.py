"""Stage, validate and atomically publish project index generations."""
import hashlib
from pathlib import Path
import shutil
import sqlite3
import time
import uuid

from ..errors import McpError
from ..storage.files import locks, private_dir
from ..storage.records import read, write
from ..library.definitions import digest, preparation_digest
from ..diagnostics.stdio import probe
from ..jobs.service import run_step, update
from .adapters import sonar, lore


def backup(source, destination):
    if Path(source).is_file():
        with sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True) as old:
            with sqlite3.connect(destination) as new:
                old.backup(new)


def prepare_tool(root, identifier, tool, request):
    root = Path(root)
    project = Path(request["project"]).resolve()
    project_id = hashlib.sha256(str(project).encode()).hexdigest()
    preparation_root = root / "managed-preparation" / request["managed"]["publisher_id"] / request["managed"]["server_id"] if request.get("managed") else root
    project_dir = preparation_root / "projects" / project_id
    if request.get("managed"):
        project_id = hashlib.sha256(str(project_dir).encode()).hexdigest()
    with locks(root / ".locks", "project-" + project_id + "-" + tool, wait=True):
        manifest = read(project_dir / "project.json", {"schema": 1, "root": str(project), "tools": {}})
        previous = manifest["tools"].get(tool)
        settings = dict(previous.get("settings", {}) if request["refresh"] and previous else {})
        settings.update(request["settings"])
        row = request["definitions"][tool]
        executable = shutil.which(row["command"])
        if not executable:
            raise McpError("validation", f"{tool} executable not found; configure its command first")
        generation = str(uuid.uuid4())
        generation_dir = project_dir / tool / generation
        private_dir(generation_dir)
        database = generation_dir / "index.sqlite"
        if previous and Path(previous["database"]).is_file():
            backup(previous["database"], database)
        adapter = sonar if tool == "sonar" else lore
        args = adapter.prepare(root=root, identifier=identifier, executable=executable,
                               row=row, database=database, project=project,
                               generation_dir=generation_dir, settings=settings, run_step=run_step)
        # Validate the corpus using ordinary metadata; no model or MCP SDK needed.
        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            indexed_root = connection.execute("SELECT value FROM metadata WHERE key='root'").fetchone()
        if not indexed_root or Path(indexed_root[0]).resolve() != project:
            raise McpError("integrity", f"{tool} prepared an index for a different project")
        update(root, identifier, step=tool + ": MCP validation")
        health = probe(row | {"command": executable, "args": args}, tool + "_status", {"check": True} if tool == "lore" else {})
        if Path(health.get("root", "")).resolve() != project:
            raise McpError("integrity", f"{tool} MCP reported a different project")
        settings.pop("install_model", None)
        prepared = {"root": str(project), "generation": generation, "database": str(database),
                    "args": args, "settings": settings, "definition_digest": digest(row),
                    "preparation_digest": preparation_digest(row), "prepared_at": time.time()}
        # Other tools can publish concurrently without losing either manifest entry.
        with locks(root / ".locks", "manifest-" + project_id, wait=True):
            manifest = read(project_dir / "project.json", {"schema": 1, "root": str(project), "tools": {}})
            manifest["tools"][tool] = prepared
            write(project_dir / "project.json", manifest)
        return prepared
