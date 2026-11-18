"""Run a real Agent Manager + Carrier + Undercurrent worker/reviewer workflow.

Uses the selected provider account for live model turns. Creates disposable projects,
trusts their startup dialogs and the generated Carrier lifecycle hooks, and stops only
its recorded executions. Provider model/approval settings are left as configured.
"""

import argparse
import asyncio
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from agent_manager import Client
from undercurrent import Client as Runtime

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--runtime-bin", type=Path, required=True)
parser.add_argument(
    "--profile", required=True, help="Existing authenticated Codex profile"
)
parser.add_argument("--timeout", type=float, default=300)
parser.add_argument('--suite-source', type=Path, default=Path(__file__).resolve().parents[3],
                    help='Manager Suite source checkout containing the shared SDK utility')
parser.add_argument('--carrier-source', type=Path, default=Path(__file__).resolve().parents[4] / 'carrier',
                    help='Carrier source checkout containing the independent artifact verifier')
args = parser.parse_args()
if args.timeout <= 0:
    parser.error("timeout must be positive")
suite_source = args.suite_source.resolve()
carrier_source = args.carrier_source.resolve()
bin = args.runtime_bin.expanduser().resolve()
for name in ('uc', 'uc-daemon', 'uc-python-bridge'):
    executable = bin / name
    if not executable.is_file() or not os.access(executable, os.X_OK):
        parser.error('Missing executable in --runtime-bin: ' + name)
for required in (suite_source / 'docs/examples/scripts/managed_agent.py',
                 carrier_source / 'examples/review_task.py',
                 carrier_source / 'docs/examples/agentic-review.md'):
    if not required.is_file():
        parser.error('Missing source dependency: ' + str(required))
root = Path(tempfile.mkdtemp(prefix="manager-live-team-"))
python = Path(sys.executable)
env = dict(
    os.environ,
    PATH=f"{bin}:{python.parent}:" + os.environ["PATH"],
    UNDERCURRENT_HOME=str(root / "runtime"),
    UNDERCURRENT_PYTHON_BRIDGE=str(bin / "uc-python-bridge"),
)
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
url = f"http://127.0.0.1:{port}"
utility = suite_source / "docs/examples/scripts/managed_agent.py"
checker = carrier_source / "examples/review_task.py"


def run(*cmd, check=True):
    p = subprocess.run(
        list(map(str, cmd)),
        env=env,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=90,
    )
    if check and p.returncode:
        raise RuntimeError(p.stderr)
    return p


def invoke(action, state, *extra):
    return run(python, utility, action, "--state", root / state, *extra)


async def ensure_ready(state, bound=False):
    intent = json.loads((root / state / "intent.json").read_text())
    async with Runtime(**intent["runtime"]) as rt:
        async with Client("codex", runtime=rt) as agents:
            agent = await agents.get(
                json.loads((root / state / "reference.json").read_text())
            )
            for _ in range(120):
                screen = (await agent.read_screen()).decode("utf8", "replace")
                (root / f"{state}-screen.txt").write_text(screen)
                if "Trust and continue" in screen:
                    await agent.send_key("enter")
                    await asyncio.sleep(1)
                elif "Hooks need review" in screen:
                    await agent.send_key("down")
                    await agent.send_key("enter")
                    await asyncio.sleep(1)
                observation = await agent.inspect()
                if observation.interaction == "available":
                    if not bound:
                        return
                    binding = json.loads(invoke("inspect", state).stdout)["carrier"]
                    if binding["status"] == "bound":
                        return
                    if not (root / state / "input-binding.json").exists():
                        prompt = root / "binding-check.txt"
                        prompt.write_text(
                            "Confirm your Carrier address and available messaging tools. Do not send messages or change files. Respond briefly."
                        )
                        invoke(
                            "send",
                            state,
                            "--message-file",
                            prompt,
                            "--input-id",
                            "binding",
                            "--timeout",
                            30,
                        )
                await asyncio.sleep(1)
            raise RuntimeError(f"Live {state} not ready/bound; inspect retained screen")


def protocol_evidence(database, worker, reviewer, artifact, digest):
    """Observe this task's actual, acknowledged, numerically correlated exchange."""
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = []
        for record in db.execute(
            "SELECT id,sender,recipient,reply_to,acknowledged_at,content FROM messages ORDER BY id"
        ):
            try:
                body = json.loads(record['content'])
            except ValueError:
                continue
            if isinstance(body, dict) and body.get('task_id') == 'revenue-001':
                rows.append(dict(record) | {'body': body})
    questions = [r for r in rows if r['sender'] == worker and r['recipient'] == reviewer
                 and r['acknowledged_at'] and r['body'].get('operation') == 'policy-question'
                 and r['body'].get('question')]
    for question in questions:
        decisions = [r for r in rows if r['sender'] == reviewer and r['recipient'] == worker
                     and r['reply_to'] == question['id'] and r['acknowledged_at']
                     and r['body'].get('decision')]
        completions = [r for r in rows if r['sender'] == worker and r['recipient'] == reviewer
                       and r['acknowledged_at'] and r['body'].get('type') == 'complete'
                       and r['body'].get('artifact_path') == str(artifact)
                       and r['body'].get('sha256') == digest]
        for complete in completions:
            approvals = [r for r in rows if r['sender'] == reviewer and r['recipient'] == worker
                         and r['reply_to'] == complete['id'] and r['acknowledged_at']
                         and r['body'].get('status') == 'accepted'
                         and r['body'].get('artifact_sha256') == digest]
            if decisions and approvals:
                return {'task_id': 'revenue-001', 'policy_question_id': question['id'],
                        'policy_reply_id': decisions[0]['id'], 'completion_id': complete['id'],
                        'accepted_review_id': approvals[0]['id'], 'artifact_sha256': digest,
                        'expected_senders': True, 'correlated_replies': True,
                        'messages_acknowledged': True}
    return None


async def watch_completion():
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        artifact = root / "revenue-001/work/report.txt"
        result = (
            run(
                python,
                checker,
                "verify",
                "--task",
                root / "revenue-001",
                "--url",
                url,
                "--credentials",
                root / "coordinator.json",
                "--reviewer",
                reviewer_address,
                "--timeout",
                1,
                check=False,
            )
            if artifact.exists()
            else None
        )
        if result and result.returncode == 0:
            (root / "verification.json").write_text(result.stdout)
            acceptance = json.loads(result.stdout)
            observed = protocol_evidence(root / 'carrier.db', worker_address, reviewer_address,
                                         artifact, acceptance['artifact_sha256'])
            if observed:
                (root / 'protocol-evidence.json').write_text(json.dumps(observed, indent=2) + '\n')
                return
        for state in ("worker", "reviewer"):
            screen = invoke("screen", state).stdout
            (root / f"{state}-screen.txt").write_text(screen)
        await asyncio.sleep(2)
    raise RuntimeError(
        "Live two-agent workflow did not satisfy independent acceptance within the configured deadline"
    )


print("Evidence:", root, flush=True)
try:
    run(bin / "uc", "daemon", "start")
    run(python, checker, "prepare", "--task", root / "revenue-001")
    for folder in ("work", "policy"):
        run("git", "init", "--quiet", root / "revenue-001" / folder)
    run(
        python,
        "-m",
        "carrier",
        "session",
        "workflow/coordinator",
        "--db",
        root / "carrier.db",
        "--credentials",
        root / "coordinator.json",
    )
    run(
        bin / "uc",
        "start",
        "carrier-live",
        "--",
        python,
        "-m",
        "carrier",
        "serve",
        "--db",
        root / "carrier.db",
        "--port",
        port,
    )
    for _ in range(100):
        try:
            with urllib.request.urlopen(url + "/health", timeout=0.3):
                break
        except Exception:
            time.sleep(0.1)
    else:
        raise RuntimeError('Private Carrier service did not become healthy')
    for state, folder in [("worker", "work"), ("reviewer", "policy")]:
        invoke(
            "launch",
            state,
            "--profile",
            args.profile,
            "--project",
            root / "revenue-001" / folder,
            "--carrier-db",
            root / "carrier.db",
            "--carrier-url",
            url,
        )
        asyncio.run(ensure_ready(state, bound=True))
        print("Ready/bound:", state, flush=True)
    # docs:start native-coordination
    observations = {
        state: json.loads(invoke("inspect", state).stdout)
        for state in ("worker", "reviewer")
    }
    reviewer_address = observations["reviewer"]["carrier"]["address"]
    worker_address = observations["worker"]["carrier"]["address"]
    doc = (carrier_source / "docs/examples/agentic-review.md").read_text()
    prompts = re.findall(r"```text\n(.*?)\n```", doc, re.S)
    if len(prompts) != 2:
        raise ValueError("Expected exactly the reviewer and worker task templates")
    for state, body in zip(("reviewer", "worker"), prompts):
        prompt = (
            body.replace("WORKER_ADDRESS", worker_address)
            .replace("REVIEWER_ADDRESS", reviewer_address)
            .replace("REPORT_PATH", str(root / "revenue-001/work/report.txt"))
        )
        path = root / f"{state}-task.txt"
        path.write_text(prompt)
        invoke(
            "send", state, "--message-file", path, "--input-id", "task", "--timeout", 30
        )
        print("Task delivered:", state, flush=True)
    asyncio.run(watch_completion())
    # docs:end native-coordination
    accepted = json.loads((root / "verification.json").read_text())
    print("Verified artifact:", (root / "revenue-001/work/report.txt").read_text().strip(), flush=True)
    print("Acceptance:", json.dumps({"expected_sender": accepted["reviewer"] == reviewer_address,
        "task_id": accepted["task_id"], "digest_checked": True}, sort_keys=True), flush=True)
finally:
    failures = []
    for state in ("worker", "reviewer"):
        if (root / state / "reference.json").exists():
            try:
                invoke("stop", state, "--timeout", 30)
            except Exception as error:
                failures.append({'execution': state, 'error_type': type(error).__name__})

    async def stop_owned_sessions():
        async with Runtime(home=root / 'runtime', bridge=str(bin / 'uc-python-bridge')) as runtime:
            observed = []
            for session in await runtime.list():
                state = await runtime.inspect(session)
                if state.lifecycle not in {'exited', 'failed', 'stopped'}:
                    await runtime.stop(session, graceful_only=True)
                    state = await runtime.wait(session, lifecycle={'exited', 'failed', 'stopped'}, timeout=30)
                observed.append({'session_id': session.id, 'lifecycle': state.lifecycle})
            (root / 'stopped-sessions.json').write_text(json.dumps(observed, indent=2) + '\n')

    try:
        # This complete runtime home is disposable and belongs solely to this run.
        asyncio.run(stop_owned_sessions())
    except Exception as error:
        failures.append({'runtime': 'owned session cleanup', 'error_type': type(error).__name__})
    daemon_stopped = False
    try:
        run(bin / 'uc', 'daemon', 'stop')
        daemon_stopped = True
    except Exception as error:
        failures.append({'daemon': 'stop', 'error_type': type(error).__name__})
    cleanup = {'owned_sessions_stopped': not failures, 'daemon_exited': daemon_stopped,
               'failures': failures}
    (root / 'cleanup.json').write_text(json.dumps(cleanup, indent=2) + '\n')
    if failures:
        raise RuntimeError('Owned process cleanup failed; inspect cleanup.json in ' + str(root))

print(
    "PASS live Codex worker + reviewer: native Carrier binding, policy question/reply, report artifact, reviewer sign-off, independent artifact/digest/sender check, verified cleanup",
    flush=True,
)
