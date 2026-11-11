"""Detached administrative finalizer, never a process or foreground observer.

The provider is a direct Undercurrent child. This worker delegates lifecycle
waiting to the Undercurrent SDK and releases run leases on terminal evidence.
"""
import argparse
import asyncio
import os
import time

from agent_manager.errors import Error
from agent_manager.manager import Manager
from agent_manager.storage.locks import locks
from agent_manager.runtime.lifecycle import observation_connection, session_missing
from agent_manager.runtime.runs import finish_run, read_run, update_run


async def supervise(manager, run_id):
    import undercurrent
    record = read_run(manager, run_id)
    target = record.get("session_id")
    connection = observation_connection(record) if target else record["connection"]
    async with undercurrent.Client(**connection) as runtime:
        if target is None:
            matches = await runtime.list(labels={"agent_manager.run_id": run_id})
            if len(matches) > 1:
                update_run(manager, run_id, status="uncertain",
                           error="Multiple runtime sessions match this run; no process was adopted")
                return
            if matches:
                target = matches[0].id
            elif record["status"] == "prepared":
                try:
                    from agent_manager.runtime.names import start
                    session = await start(manager, run_id, runtime)
                    target = session.id
                except undercurrent.UndercurrentError as error:
                    if read_run(manager, run_id)["status"] == "prepared":
                        # A failed read-only name check has not submitted work.
                        raise
                    if error.effect_certainty == "known_absent" or error.category == "InvalidArgument":
                        finish_run(manager, run_id, status="failed", error="Undercurrent rejected the launch before effect")
                        return
                    update_run(manager, run_id, status="uncertain", error="Launch delivery uncertain; reconcile before retrying")
                    raise
            else:
                update_run(manager, run_id, status="uncertain", error="No matching runtime session yet; launch was not replayed")
                return
            update_run(manager, run_id, session_id=target, status="running", error=None)
        while True:
            try:
                await runtime.wait(target, lifecycle=("stopped", "exited", "failed"),
                                   timeout=300)
                break
            except TimeoutError:
                continue
            except undercurrent.UndercurrentError as error:
                if not session_missing(error):
                    raise
                finish_run(manager, run_id, status="finished", error="Runtime session no longer exists")
                return
        finish_run(manager, run_id, status="finished")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider")
    parser.add_argument("root")
    parser.add_argument("run_id")
    parser.add_argument("--config")
    args = parser.parse_args()
    manager = Manager(args.provider, root=args.root, config=args.config)
    try:
        with locks(manager.lock_root, "sdk-" + args.run_id):
            update_run(manager, args.run_id, worker_pid=os.getpid())
            # Connection failure leaves the lease intact and retries. A new
            # client can also reconcile after this worker or the host restarts.
            retry_delay = 1
            while True:
                state = manager.state()
                if args.run_id not in state.get("runs", {}): return
                try:
                    asyncio.run(supervise(manager, args.run_id))
                    return
                except Exception as error:
                    # Retain diagnostics without recording provider payloads or auth.
                    update_run(manager, args.run_id, worker_error=type(error).__name__)
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 30)
    except Error as error:
        if str(error).startswith("Busy:"): return
        raise


if __name__ == "__main__": main()
