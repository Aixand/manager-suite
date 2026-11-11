"""Opt-in real OpenCode UI regression, without prompts or account credentials.

Set OPENCODE_LIVE=1 and UC_TEST_BIN to the directory containing the three
Undercurrent binaries. The undercurrent Python SDK must be importable.
"""
import asyncio
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from agent_manager import Client
from agent_manager.runtime.runs import read_run


@unittest.skipUnless(os.environ.get('OPENCODE_LIVE') == '1' and os.environ.get('UC_TEST_BIN')
                     and shutil.which('opencode'), 'Opt-in real OpenCode/Undercurrent test')
class OpenCodeDirectoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_interactive_launches_keep_their_requested_directories(self):
        import undercurrent
        binaries = Path(os.environ['UC_TEST_BIN']).resolve()
        with tempfile.TemporaryDirectory(prefix='am-cwd-') as temporary:
            base = Path(temporary)
            home = base / 'runtime'
            home.mkdir(mode=0o700)
            daemon = subprocess.Popen([str(binaries / 'uc-daemon')],
                env=os.environ | {'UNDERCURRENT_HOME': str(home)}, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            runtime = undercurrent.Client(home=home, bridge=str(binaries / 'uc-python-bridge'), request_timeout=15)
            launched = []
            client = Client('opencode', runtime=runtime, root=base / 'profiles')
            try:
                async with asyncio.timeout(15):
                    while not (home / 'run/control.sock').exists():
                        self.assertIsNone(daemon.poll(), 'Diagnostic daemon exited')
                        await asyncio.sleep(.1)
                await runtime.connect()
                await client.create_profile('probe')
                for label in ('project-a', 'project-b'):
                    target = base / label
                    target.mkdir()
                    agent = await client.spawn(profile='probe', cwd=target,
                        env={'PWD': str(Path.cwd())}, model='opencode/space-bunny-free', timeout=30)
                    launched.append((agent, target))
                for agent, target in launched:
                    record = read_run(client.manager, agent.run_id)
                    self.assertEqual(record['launch']['cwd'], str(target))
                    async with asyncio.timeout(20):
                        while True:
                            # Read the UI's output directly: terminal snapshots can
                            # be temporarily unavailable during its initial redraw.
                            screen = (await runtime.read_output(agent.session_id)).text()
                            if target.name in screen:
                                self.assertNotIn('oc mini', screen)
                                self.assertEqual(record['launch']['command'], [shutil.which('opencode'), '--standalone'])
                                break
                            await asyncio.sleep(.2)
            finally:
                try:
                    for agent, _ in launched:
                        await runtime.stop(agent.session_id, force=True)
                    # Let the managed-run finalizers finish before removing homes.
                    async with asyncio.timeout(10):
                        while client.manager.state().get('runs'):
                            await asyncio.sleep(.1)
                finally:
                    await runtime.close()
                    daemon.terminate()
                    try:
                        daemon.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        daemon.kill()
                        daemon.wait()
