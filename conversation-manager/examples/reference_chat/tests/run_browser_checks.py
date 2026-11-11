"""Isolated offline browser checks. Never discovers or calls signed-in providers."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def main():
    artifacts = ROOT / '.artifacts'
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='reference-chat-checks-') as state:
        process = subprocess.Popen([sys.executable, str(ROOT / 'app.py'), '--port', '0',
            '--state', state, '--stop-workers-on-exit'], stdout=subprocess.PIPE,
            stderr=(artifacts / 'server.log').open('w'), text=True)
        try:
            line = process.stdout.readline()
            match = re.search(r'http://127\.0\.0\.1:(\d+)', line)
            if not match:
                raise RuntimeError('Server did not start; inspect .artifacts/server.log')
            env = {**os.environ, 'REFERENCE_CHAT_URL': match.group(0),
                   'REFERENCE_CHAT_ARTIFACTS': str(artifacts)}
            for script in ('browser_test.py', 'queue_browser_test.py', 'tool_queue_browser_test.py', 'recovery_browser_test.py'):
                subprocess.run([sys.executable, str(ROOT / 'tests' / script)], env=env, check=True)
        finally:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == '__main__':
    main()
