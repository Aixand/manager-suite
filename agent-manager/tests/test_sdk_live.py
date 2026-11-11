"""Opt-in local-daemon integration; uses a fake provider and no real credentials."""
import asyncio
import json
import os
from pathlib import Path
import pty
import signal
import subprocess
import sys
import tempfile
import termios
import time
import unittest
import uuid

from agent_manager import Client, Manager
from tests.support import native_manifest

BIN = os.environ.get("UC_TEST_BIN")

PROVIDER = r'''#!/usr/bin/env python3
import os, pathlib, sys, time
home = pathlib.Path(os.environ.get('TEST_AGENT_HOME') or os.environ['CODEX_HOME'])
if 'login' in sys.argv:
    (home / 'native-login-marker').write_text('native-sign-in')
    raise SystemExit(0)
messages = []
def ready():
    print('\x1b[2J\x1b[H' + '\n'.join(messages) + '\n› \n  gpt-test high ' + os.getcwd(), flush=True)
ready()
for line in sys.stdin:
    print('\x1b[2J\x1b[HWorking\nesc to interrupt', flush=True)
    time.sleep(.35)
    # Model a terminal editor consuming bracketed-paste framing.
    line = line.replace('\x1b[200~', '').replace('\x1b[201~', '')
    messages.append('RESULT:' + line.strip())
    (home / 'native-login-marker').write_text('native-updated')
    ready()
'''


@unittest.skipUnless(BIN, "Set UC_TEST_BIN to the directory containing uc-daemon, uc-session-host and uc-python-bridge")
class LiveSDKTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import undercurrent
        self.undercurrent = undercurrent
        self.temp = tempfile.TemporaryDirectory(prefix="uc-py-")
        self.base = Path(self.temp.name)
        user = self.base / 'user'; user.mkdir()
        from unittest.mock import patch
        self.enterContext(patch.dict(os.environ, HOME=str(user), USERPROFILE=str(user)))
        self.home = self.base / "runtime"
        self.home.mkdir(mode=0o700)
        self.log = (self.base / "daemon.log").open("w+")
        self.daemon = subprocess.Popen([str(Path(BIN) / "uc-daemon")],
            env=os.environ | {"UNDERCURRENT_HOME": str(self.home)},
            stdin=subprocess.DEVNULL, stdout=self.log, stderr=self.log)
        self.addAsyncCleanup(self.cleanup)
        deadline = time.monotonic() + 15
        while not (self.home / "run" / "control.sock").exists():
            if self.daemon.poll() is not None:
                self.log.seek(0)
                self.fail(self.log.read()[-4000:])
            if time.monotonic() > deadline: self.fail("Daemon did not create its socket")
            await asyncio.sleep(.05)
        self.runtime = self.runtime_client()
        await self.runtime.connect()
        self.addAsyncCleanup(self.runtime.close)
        self.provider = self.base / "provider"
        self.provider.write_text(PROVIDER)
        self.provider.chmod(0o700)
        self.config = self.base / "providers.toml"
        self.config.write_text('[providers.test]\n' + '\n'.join([
            f'executable = {json.dumps(str(self.provider))}',
            f'native_home = {json.dumps(str(self.base / "native"))}',
        ]))
        definitions = self.base / 'providers'; definitions.mkdir()
        (definitions / 'test.toml').write_text(native_manifest(home=self.base / 'native'))
        self.manager = Manager("test", config=self.config, root=self.base / "agents")
        self.manager.new_profile("work")
        self.manager.login("work")
        self.agents = Client("test", runtime=self.runtime, config=self.config, root=self.base / "agents")

    def runtime_client(self):
        return self.undercurrent.Client(home=self.home, bridge=str(Path(BIN) / "uc-python-bridge"), request_timeout=10)

    async def cleanup(self):
        # Stop only this test's sessions before stopping its isolated daemon.
        if self.daemon.poll() is None:
            try:
                async with self.runtime_client() as runtime:
                    for session in await runtime.list():
                        if session.lifecycle not in ("stopped", "failed", "exited"):
                            await runtime.stop(session, force=True)
                    if hasattr(self, "manager"):
                        async with asyncio.timeout(10):
                            while self.manager.state().get("runs"):
                                await asyncio.sleep(.05)
            finally:
                self.daemon.terminate()
                try: await asyncio.to_thread(self.daemon.wait, timeout=10)
                except subprocess.TimeoutExpired:
                    self.daemon.kill()
                    await asyncio.to_thread(self.daemon.wait)
        self.log.close()
        self.temp.cleanup()

    async def ready(self, agent, **kwargs):
        return await self.runtime.wait_for_stability(agent.session_id, lifecycle="running",
            interaction="available", stable_for=2.2, timeout=15, **kwargs)

    async def test_claude_native_profile_opens_a_real_terminal_with_initial_prompt(self):
        provider = self.base / 'offline-claude'
        provider.write_text('#!/usr/bin/env python3\nimport json, sys\n'
            'print("INITIAL:" + json.dumps(sys.argv[1:]), flush=True)\n'
            'for line in sys.stdin:\n    print("RESULT:" + line.strip(), flush=True)\n')
        provider.chmod(0o700)
        with self.config.open('a') as config:
            config.write('\n[providers.claude]\nexecutable=' + json.dumps(str(provider)) +
                         '\nnative_home=' + json.dumps(str(self.base / 'claude-native')) + '\n')
        claude = Client('claude', runtime=self.runtime, config=self.config, root=self.base / 'agents')
        manager = claude.manager
        manager.new_profile('work')
        prompt = 'Review the project with "quotes", `code` and $(text) intact'
        agent = await claude.spawn(profile='work', cwd=self.base, args=['--', prompt], detection=False, timeout=15)
        try:
            self.assertTrue(agent.session_id)
            self.assertEqual((await self.runtime.inspect(agent.session_id)).lifecycle, 'running')
            async with asyncio.timeout(10):
                while True:
                    screen = await self.runtime.read_screen(agent.session_id)
                    if b'INITIAL:' in screen:
                        break
                    await asyncio.sleep(.05)
            output = await self.runtime.read_output(agent.session_id)
            arguments = json.loads(output.data.decode().split('INITIAL:', 1)[1].splitlines()[0])
            self.assertEqual(arguments[-2:], ['--', prompt])
        finally:
            await self.runtime.stop(agent.session_id, force=True)
            async with asyncio.timeout(10):
                while manager.state().get('runs'):
                    await asyncio.sleep(.05)

    async def test_antigravity_opens_a_real_terminal_with_initial_prompt_without_api_credentials(self):
        provider = self.base / 'offline-antigravity'
        provider.write_text('#!/usr/bin/env python3\nimport json, sys\n'
            'print("INITIAL:" + json.dumps(sys.argv[1:]), flush=True)\n'
            'print("Finish Antigravity sign-in in this terminal", flush=True)\n'
            'for line in sys.stdin:\n    print("RESULT:" + line.strip(), flush=True)\n')
        provider.chmod(0o700)
        with self.config.open('a') as config:
            config.write('\n[providers.antigravity]\nexecutable=' + json.dumps(str(provider)) +
                         '\n')
        antigravity = Client('antigravity', runtime=self.runtime, config=self.config, root=self.base / 'agents')
        manager = antigravity.manager
        manager.new_profile('work')
        self.assertFalse(manager.state().get('sdk_credentials'))
        self.assertFalse(manager.state().get('native_login_receipts'))
        prompt = 'Review the project with "quotes", `code` and $(text) intact'
        agent = await antigravity.spawn(profile='work', cwd=self.base, args=['--prompt-interactive', prompt], detection=False, timeout=15)
        try:
            self.assertTrue(agent.session_id)
            self.assertEqual((await self.runtime.inspect(agent.session_id)).lifecycle, 'running')
            async with asyncio.timeout(10):
                while True:
                    screen = await self.runtime.read_screen(agent.session_id)
                    if b'Finish Antigravity sign-in' in screen:
                        break
                    await asyncio.sleep(.05)
            output = await self.runtime.read_output(agent.session_id)
            arguments = json.loads(output.data.decode().split('INITIAL:', 1)[1].splitlines()[0])
            self.assertEqual(arguments[-2:], ['--prompt-interactive', prompt])
        finally:
            await self.runtime.stop(agent.session_id, force=True)
            async with asyncio.timeout(10):
                while manager.state().get('runs'):
                    await asyncio.sleep(.05)

    async def test_two_prompts_disconnect_reconnect_and_native_file_preservation(self):
        agent = await self.agents.spawn(profile="work", cwd=self.base, detection_profile="codex", timeout=15)
        await self.ready(agent)
        first = await self.runtime.send_input(agent.session_id, "research", delivery_id="research-1")
        await self.ready(agent, after_revision=first.foreground_revision)
        self.assertIn(b"RESULT:research", await self.runtime.read_screen(agent.session_id))
        duplicate = await self.runtime.send_input(agent.session_id, "research", delivery_id="research-1")
        self.assertTrue(duplicate.duplicate)
        with self.assertRaises(self.undercurrent.UndercurrentError):
            await self.runtime.send_input(agent.session_id, "different prompt", delivery_id="research-1")
        with self.assertRaises(self.undercurrent.UndercurrentError) as rejected:
            await self.runtime.send_input(agent.session_id, "stale prompt", expected_revision=0)
        self.assertEqual(rejected.exception.product_category, "revision_precondition_failed")
        self.assertEqual(rejected.exception.effect_certainty, "known_absent")

        await self.runtime.close()
        self.runtime = self.runtime_client()
        await self.runtime.connect()
        self.addAsyncCleanup(self.runtime.close)
        agents = Client("test", runtime=self.runtime, config=self.config, root=self.base / "agents")
        restored = await agents.get(agent.run_id)
        self.assertEqual(restored.session_id, agent.session_id)
        self.assertEqual((await self.runtime.inspect(agent.session_id)).lifecycle, "running")
        second = await self.runtime.send_input(agent.session_id, "summarize")
        await self.ready(agent, after_revision=second.foreground_revision)
        screen = await self.runtime.read_screen(agent.session_id)
        self.assertIn(b"RESULT:summarize", screen)
        output = await self.runtime.read_output(agent.session_id)
        self.assertIn(b"RESULT:research", output.data)

        await self.runtime.stop(agent.session_id)
        async with asyncio.timeout(10):
            while self.manager.state().get("runs"):
                await asyncio.sleep(.05)
        self.assertEqual((self.manager.home("work") / "native-login-marker").read_text(), "native-updated")
        self.assertEqual((await agents.get(agent.run_id)).session_id, agent.session_id)

    async def test_labels_and_private_metadata_survive_runtime_and_reconnect(self):
        scoped = self.agents.with_context(labels={"app": "pathspec", "workflow.run_id": "one"}, metadata={"private": "retained-context"})
        agent = await scoped.spawn(profile="work", cwd=self.base, detection_profile="codex", detection=False, timeout=15)
        sessions = await self.runtime.list(labels={"app": "pathspec", "workflow.run_id": "one"})
        self.assertEqual([session.id for session in sessions], [agent.session_id])
        self.assertEqual(sessions[0].raw["labels"]["workflow.run_id"], "one")
        self.assertNotIn("retained-context", json.dumps([session.raw for session in sessions]))
        self.assertEqual(await self.runtime.metadata(agent.session_id), {"private": "retained-context"})
        rows = await scoped.runs(labels={"app": "pathspec"})
        self.assertNotIn("retained-context", json.dumps(rows))
        self.assertEqual(rows[0]["labels"]["workflow.run_id"], "one")
        reconnect = Client("test", runtime=self.runtime, config=self.config, root=self.base / "agents")
        self.assertEqual((await reconnect.get(agent.run_id)).labels["app"], "pathspec")
        await self.runtime.stop(agent.session_id, force=True)
        async with asyncio.timeout(10):
            while self.manager.state().get("runs"):
                await asyncio.sleep(.05)
        self.assertEqual(await reconnect.metadata(agent.run_id), {"private": "retained-context"})
        self.assertEqual(len(await reconnect.runs(labels={"app": "pathspec"})), 1)

    async def test_cancelled_wait_does_not_stop_process_or_other_requests(self):
        agent = await self.agents.spawn(profile="work", cwd=self.base, detection_profile="codex", timeout=15)
        await self.ready(agent)
        waiter = asyncio.create_task(self.runtime.wait(agent.session_id, lifecycle="exited", timeout=60))
        await asyncio.sleep(.1)
        self.assertEqual((await self.runtime.inspect(agent.session_id)).lifecycle, "running")
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError): await waiter
        self.assertEqual((await self.runtime.inspect(agent.session_id)).lifecycle, "running")
        await self.runtime.stop(agent.session_id, force=True)

    async def test_worker_crash_can_reconcile_without_relaunching_agent(self):
        agent = await self.agents.spawn(profile="work", cwd=self.base, detection_profile="codex", timeout=15)
        await self.ready(agent)
        record = self.manager.state()["runs"][agent.run_id]
        worker_pid = record["worker_pid"]
        os.kill(worker_pid, signal.SIGKILL)
        # The killed administrative worker does not own the provider process.
        self.assertEqual((await self.runtime.inspect(agent.session_id)).lifecycle, "running")
        await asyncio.sleep(.1)
        await self.agents.reconcile()
        async with asyncio.timeout(10):
            while self.manager.state()["runs"][agent.run_id]["worker_pid"] == worker_pid:
                await asyncio.sleep(.05)
        sessions = await self.runtime.list(labels={"agent_manager.run_id": agent.run_id})
        self.assertEqual([session.id for session in sessions], [agent.session_id])
        await self.runtime.stop(agent.session_id, force=True)
        async with asyncio.timeout(10):
            while self.manager.state().get("runs"):
                await asyncio.sleep(.05)

    async def test_cli_configured_runtime_attaches_detaches_and_preserves_native_files(self):
        if not (Path(BIN) / 'uc').is_file():
            self.skipTest('Build uc to test interactive CLI attachment')
        self.config.write_text(self.config.read_text().replace('[providers.test]', '[providers.codex]')
                               + '\nruntime = "undercurrent"\n')
        self.manager = Manager('codex', config=self.config, root=self.base / 'agents')
        self.manager.new_profile('work')
        self.manager.login('work')
        project = Path(__file__).resolve().parents[1]
        python_path = os.pathsep.join([str(project), str(Path(self.undercurrent.__file__).resolve().parents[1])])
        master, slave = pty.openpty()
        termios.tcsetwinsize(slave, (40, 120))
        os.set_blocking(master, False)
        env = os.environ | {'UNDERCURRENT_HOME': str(self.home),
                            'UNDERCURRENT_PYTHON_BRIDGE': str(Path(BIN) / 'uc-python-bridge'),
                            'PATH': str(Path(BIN)) + os.pathsep + os.environ['PATH'],
                            'PYTHONPATH': python_path, 'TERM': 'xterm-256color'}
        process = subprocess.Popen([sys.executable, '-m', 'agent_manager', '--config', str(self.config),
                                    '--home', str(self.base / 'agents'), 'codex', 'run', 'work'],
                                   env=env, cwd=self.base, stdin=slave, stdout=slave, stderr=slave,
                                   start_new_session=True)
        os.close(slave)
        output = bytearray()

        async def screen_contains(text):
            async with asyncio.timeout(20):
                while text not in output:
                    try:
                        output.extend(os.read(master, 65536))
                    except BlockingIOError:
                        pass
                    except OSError:
                        self.fail(output.decode('utf-8', 'replace'))
                    if process.poll() is not None:
                        self.fail(output.decode('utf-8', 'replace'))
                    await asyncio.sleep(.05)

        try:
            await screen_contains(b'gpt-test')
            os.write(master, b'hello\r')
            await screen_contains(b'RESULT:hello')
            os.write(master, b'\x1bud')
            self.assertEqual(await asyncio.to_thread(process.wait, timeout=10), 0)
            run_id, run = next(iter(self.manager.state()['runs'].items()))
            self.assertEqual(run['runtime'], 'undercurrent')
            self.assertEqual((await self.runtime.inspect(run['session_id'])).lifecycle, 'running')
            await self.runtime.stop(run['session_id'], force=True)
            async with asyncio.timeout(10):
                while self.manager.state().get('runs'):
                    await asyncio.sleep(.05)
            self.assertEqual((self.manager.home('work') / 'native-login-marker').read_text(), 'native-updated')
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                await asyncio.to_thread(process.wait, timeout=10)
            os.close(master)

    async def test_execution_handle_auto_start_input_detach_reference_recovery_and_stop(self):
        self.manager.set_default('work')
        key = str(uuid.uuid4())
        agent = await self.agents.start(cwd=self.base, idempotency_key=key,
                                       detection_profile='codex', timeout=15)
        await self.ready(agent)
        state = await agent.inspect()
        self.assertEqual(state.lifecycle, 'running')
        self.assertEqual(state.source, 'undercurrent')
        self.assertEqual(agent.selection.profile, 'work')
        self.assertIn('default', agent.selection.reason)
        receipt = await agent.send_input('handle-roundtrip', delivery_id='handle-first')
        await self.ready(agent, after_revision=receipt.foreground_revision)
        self.assertIn(b'RESULT:handle-roundtrip', await agent.read_screen())
        duplicate = await agent.send_input('handle-roundtrip', delivery_id='handle-first')
        self.assertTrue(duplicate.duplicate)
        await self.ready(agent, after_revision=receipt.foreground_revision)
        page = await agent.read_output(max_bytes=65536)
        self.assertIn(b'RESULT:handle-roundtrip', page.data)
        saved = json.loads(json.dumps(agent.reference.to_dict()))
        await self.agents.close()  # borrowed runtime: does not stop the process
        await self.runtime.close()
        self.runtime = self.runtime_client()
        await self.runtime.connect()
        self.addAsyncCleanup(self.runtime.close)
        client = Client('test', runtime=self.runtime, config=self.config, root=self.base / 'agents')
        recovered = await client.get(saved)
        same = await client.start(cwd=self.base, idempotency_key=key, detection_profile='codex', timeout=15)
        self.assertEqual(recovered.reference, same.reference)
        self.assertEqual((await recovered.inspect()).lifecycle, 'running')
        second = await recovered.send_input('recovered-input', delivery_id='handle-second')
        await self.ready(recovered, after_revision=second.foreground_revision)
        await recovered.wait(interaction='available', timeout=15)
        self.assertIn(b'RESULT:recovered-input', await recovered.read_screen())
        await recovered.stop(force=True)
        ended = await recovered.wait(lifecycle=('stopped', 'exited', 'failed'), timeout=15)
        self.assertIn(ended.lifecycle, ('stopped', 'exited', 'failed'))
