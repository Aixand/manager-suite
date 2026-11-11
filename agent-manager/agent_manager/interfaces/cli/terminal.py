"""Interactive CLI launches backed by the SDK's durable Undercurrent runs."""
import asyncio
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from agent_manager.errors import Error
from agent_manager.storage.locks import locks
from agent_manager.storage.files import private_dir
from agent_manager.runtime.names import allocate_name
from agent_manager.interfaces import sdk


def dependency():
    try:
        import undercurrent
    except ImportError as error:
        raise Error("Undercurrent runtime requires aixand-agent-manager[undercurrent]; install it in the CLI's Python environment") from error
    return undercurrent


def preflight():
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise Error("Undercurrent CLI launches require an interactive terminal; use Client.spawn() for background launches")
    if shutil.which("uc") is None:
        raise Error("Undercurrent CLI executable 'uc' was not found on PATH")
    dependency()


async def start(manager, run_id, args, *, extra_env=None):
    undercurrent = dependency()
    try:
        async with undercurrent.Client() as runtime:
            # Connect before converting the ordinary CLI lease to a durable run.
            # Missing bridges/daemons cannot leave a queued launch behind.
            await asyncio.to_thread(sdk.reconcile_runs, manager)
            program = shutil.which(manager.config["executable"])
            if program is None:
                raise Error("Configured provider executable was not found")
            columns, rows = shutil.get_terminal_size()
            directory = manager.working_directory()
            with locks(manager.lock_root, "registry", wait=True):
                state = manager.state()
                record = state["runs"][run_id]
                home = Path(record["home"]).resolve()
                private_dir(home)
                runtime_name = record.get("runtime_name") or allocate_name(state, record["agent"], run_id)
                variables = manager.launch_environment(home, cwd=directory, base={"TERM": os.environ.get("TERM", "xterm-256color"),
                    "COLORTERM": os.environ.get("COLORTERM", "truecolor"),
                    **getattr(manager, "_launch_env", {}), **(extra_env or {})})
                from agent_manager.runtime.native.launch import terminal_launch
                native_args, variables = terminal_launch(manager, args, variables, managed=True)
                record.update(runtime="undercurrent", home=str(home), status="prepared", session_id=None,
                    runtime_name=runtime_name,
                    connection=runtime.connection_options,
                    launch={"command": manager.launch_command(home, [os.path.abspath(program), *native_args]),
                            "name": runtime_name, "cwd": str(directory),
                            "env": variables,
                            "columns": columns, "rows": rows,
                            "profile": manager.config["detection_profile"],
                            "labels": record.get("labels", {"agent_manager.run_id": run_id, "agent_manager.profile": record["agent"]}),
                            **({"metadata": record["metadata"]} if record.get("metadata") else {}),
                            "idempotency_key": run_id})
                manager.save(state)
            try:
                sdk.launch_worker(manager, run_id)
            except OSError as error:
                raise sdk.LaunchError("Could not start the administrative worker; the managed run was retained", run_id) from error
            record = await sdk.wait_for_launch(manager, run_id)
            return record
    except undercurrent.UndercurrentError as error:
        raise Error(f"Cannot connect to Undercurrent: {error}. Check the bridge and start the daemon with 'uc daemon start'.") from error


def launch(manager, run_id, args, *, extra_env=None):
    record = asyncio.run(start(manager, run_id, args, extra_env=extra_env))
    session_name = record["launch"]["name"]
    runtime_home = record["connection"]["home"]
    reconnect = shlex.join(["env", f"UNDERCURRENT_HOME={runtime_home}", "uc", "attach", session_name])
    print(f"Undercurrent session: {session_name}\nReattach: {reconnect}", file=sys.stderr, flush=True)
    return subprocess.call(["uc", "attach", session_name], env=os.environ | {"UNDERCURRENT_HOME": runtime_home})
