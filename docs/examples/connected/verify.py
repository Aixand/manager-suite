"""Run nine connected examples against real local services; never calls models.

python docs/examples/connected/verify.py --runtime-bin /path/to/undercurrent/bin
Evidence and logs remain in the printed disposable directory.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

EXAMPLES = ('prompt_worker', 'frozen_prompt', 'artifact_audit', 'skill_rollout',
            'batch_pipeline', 'mcp_job', 'conversation_jobs', 'agent_mailbox', 'parallel_gate')
CLEAN = {'daemon_exited': True, 'failures': [], 'owned_sessions_stopped': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-bin', type=Path, required=True)
    parser.add_argument('--only', choices=EXAMPLES)
    args = parser.parse_args()
    folder = Path(__file__).resolve().parent
    # Keep Unix control socket paths short, including the SDK's own suffix.
    evidence = Path(tempfile.mkdtemp(prefix='mc-verify-'))
    print('Evidence:', evidence, flush=True)
    results = []
    for index, name in enumerate((args.only,) if args.only else EXAMPLES):
        work = evidence / str(index)
        log = evidence / (name + '.log')
        with log.open('w') as output:
            process = subprocess.run(
                [sys.executable, '-B', str(folder / (name + '.py')),
                 '--work', str(work), '--runtime-bin', str(args.runtime_bin.resolve())],
                stdout=output, stderr=subprocess.STDOUT,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), timeout=120)
        cleanup = json.loads((work / 'cleanup.json').read_text()) if (work / 'cleanup.json').is_file() else None
        ok = process.returncode == 0 and cleanup == CLEAN
        results.append(dict(example=name, passed=ok, exit_code=process.returncode, cleanup=cleanup))
        (evidence / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
        print(('PASS ' if ok else 'FAIL ') + name, flush=True)
        if not ok:
            raise SystemExit('Inspect ' + str(log) + ' and owned process evidence before retrying')
    print('All selected workflows passed; owned processes stopped.', flush=True)


if __name__ == '__main__':
    main()
