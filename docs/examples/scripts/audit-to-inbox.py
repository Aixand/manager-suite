"""Compose Prompt + Skill Manager, run a CSV audit with UC, and deliver via Carrier.

Uses a fresh --work directory and existing Carrier credentials. No provider call.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

from carrier import Client as Inbox
from prompt_manager import Manager as Prompts
from skill_manager import Manager as Skills
from undercurrent import Client as Runtime

AUDITOR = '''import csv, json, sys
from pathlib import Path
with open(sys.argv[1], newline="", encoding="utf-8") as stream:
    rows = list(csv.DictReader(stream))
missing = [index for index, row in enumerate(rows, 2) if not row["email"].strip()]
report = {"rows": len(rows), "missing_email_records": missing,
          "status": "needs-review" if missing else "passed"}
Path(sys.argv[2]).write_text(json.dumps(report, sort_keys=True) + "\\n")
'''


async def main(args):
    root = args.work.resolve()
    # Refuse to overwrite a prior run's evidence or resources.
    root.mkdir(parents=True, exist_ok=False)
    task = "Audit contacts.csv for missing email addresses. Report CSV record numbers."
    prompts = Prompts(home=root / "prompts", packs=[])
    prompts.create_managed("csv-audit", content=task, role="user")
    rendered = prompts.render_managed("local", "csv-audit", "csv-audit", format="messages")
    prompt_file = root / "request.json"
    prompt_file.write_text(json.dumps(rendered["output"], sort_keys=True) + "\n")

    # Publish a complete local skill, then explicitly deliver its selected content.
    publisher = root / "publisher" / "csv-audit"
    (publisher / "scripts").mkdir(parents=True)
    (publisher / "SKILL.md").write_text(
        "---\nname: csv-audit\ndescription: Audit a contacts CSV for missing email addresses.\n"
        "---\nRun scripts/audit.py with the input CSV and output JSON paths.\n"
    )
    (publisher / "scripts" / "audit.py").write_text(AUDITOR)
    skills = Skills(root / "skills-store")
    skill = skills.skill("contacts-app", "csv-audit")
    skill.register(publisher, publisher_revision=1)
    project = root / "project"
    project.mkdir()
    selection = skills.project(project)
    plan = selection.preview_connect([skill.reference], skills_dir="skills")
    selection.connect(plan)
    script = project / "skills" / "csv-audit" / "scripts" / "audit.py"
    csv = project / "contacts.csv"
    csv.write_text("name,email\nAda,ada@example.test\nLin,\n")
    output = project / "audit.json"
    evidence = {"prompt_sha256": hashlib.sha256(prompt_file.read_bytes()).hexdigest(),
                "script_sha256": hashlib.sha256(script.read_bytes()).hexdigest()}
    (root / "selection.json").write_text(json.dumps(evidence, indent=2) + "\n")

    # The application chooses this executable; Skill Manager never runs it.
    async with Runtime() as runtime:
        session = await runtime.start(
            [sys.executable, str(script), str(csv), str(output)],
            cwd=str(project), detection=False,
        )
        (root / "execution.json").write_text(json.dumps({"session_id": session.id}) + "\n")
        print(f"Audit session: {session.id}", flush=True)
        finished = await runtime.wait(session, lifecycle={"exited", "failed", "stopped"}, timeout=30)
        if finished.lifecycle != "exited" or finished.raw["lifecycle"].get("code") != 0:
            raise RuntimeError(f"Audit failed; inspect session {session.id}")

    report = json.loads(output.read_text())
    if report != {"rows": 2, "missing_email_records": [3], "status": "needs-review"}:
        raise RuntimeError("The audit artifact did not match the fixture's acceptance check")
    # Keep exact delivery bytes and key before dispatch, so uncertainty is recoverable.
    delivery = {"report": report, "evidence": evidence, "session_id": session.id}
    body = json.dumps(delivery, sort_keys=True)
    key = "audit:" + hashlib.sha256(body.encode()).hexdigest()
    intent = {"to": args.to, "content": body, "idempotency_key": key}
    (root / "delivery.json").write_text(json.dumps(intent, indent=2) + "\n")
    async with Inbox(url=args.url, credentials=args.credentials) as sender:
        message = await sender.send(body, to=args.to, idempotency_key=key)
        (root / "receipt.json").write_text(json.dumps({"message_id": message.id}) + "\n")
        print(json.dumps({"message_id": message.id, "report": report}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True, help="New directory for this run")
    parser.add_argument("--url", default="http://127.0.0.1:8877")
    parser.add_argument("--credentials", required=True)
    parser.add_argument("--to", default="sdk/audit-reviewer")
    asyncio.run(main(parser.parse_args()))
