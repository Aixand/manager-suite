"""Isolated real daemon, retained workers, native Codex fixture and OpenCode."""

import asyncio
import json
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

from conversation_manager import ConversationClient, ConversationError
from conversation_manager.launcher import process_start
import test_codex_local as codex_tests
import test_opencode_frontend as opencode_tests

BINS = Path(os.environ.get("UC_TEST_BIN", "/missing"))
ENABLED = (
    os.name == "posix"
    and (BINS / "uc-daemon").is_file()
    and codex_tests.Manager is not None
    and importlib.util.find_spec("undercurrent") is not None
)


class DaemonMixin:
    async def start_daemon(self):
        self.daemon_home = self.root / "uc"
        self.daemon = subprocess.Popen(
            [str(BINS / "uc-daemon")],
            env={**os.environ, "UNDERCURRENT_HOME": str(self.daemon_home)},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        async with asyncio.timeout(15):
            while not (self.daemon_home / "run/control.sock").exists():
                if self.daemon.poll() is not None:
                    self.fail("Isolated daemon exited")
                await asyncio.sleep(0.03)
        self.options = dict(
            home=str(self.daemon_home), bridge=str(BINS / "uc-python-bridge")
        )

    def new_client(self):
        return ConversationClient(
            self.client.home,
            agent_config=self.config,
            agent_root=self.root / "agents",
            runtime="undercurrent",
            runtime_options=self.options,
        )

    async def stop_daemon(self):
        self.daemon.terminate()
        try:
            await asyncio.wait_for(asyncio.to_thread(self.daemon.wait), 10)
        except TimeoutError:
            self.daemon.kill()
            await asyncio.to_thread(self.daemon.wait)


@unittest.skipUnless(
    ENABLED, "Set UC_TEST_BIN to isolated native Undercurrent binaries"
)
class CodexRuntimeTests(DaemonMixin, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await codex_tests.LocalCodexTests.asyncSetUp(self)
        await self.start_daemon()
        self.client = self.new_client()
        self.startups = []
        self.handles = []

    async def asyncTearDown(self):
        try:
            for handle in self.handles:
                await handle.disconnect()
            for operation in self.startups:
                result = await ConversationClient(self.client.home).stop_startup(
                    operation, operation_id="cleanup:" + operation
                )
                ref = result.get("recovery", {}).get("runtime_ref")
                if ref:
                    async with asyncio.timeout(10):
                        while (await self.client.inspect_runtime(ref))["state"] not in (
                            "stopped",
                            "exited",
                            "failed",
                        ):
                            await asyncio.sleep(0.03)
        finally:
            await self.stop_daemon()
            self.tmp.cleanup()

    async def start(self, key="start", process=None):
        agent = (await self.client.available_agents(providers=("codex",)))[0][
            "reference"
        ]
        self.startups.append(key)
        chat = await self.client.start_conversation(
            agent=agent, cwd=self.root, operation_id=key, process=process
        )
        self.handles.append(chat)
        return chat

    async def test_named_native_worker_metadata_rename_reconnect_and_child_cleanup(
        self,
    ):
        chat = await self.start(
            process={
                "name": "Named Codex",
                "labels": {"project": "demo"},
                "metadata": {"purpose": "qualification"},
            }
        )
        ref = chat.reference["recovery"]["connection_ref"]["runtime_ref"]
        self.assertEqual(ref["owner"], "conversation-manager-undercurrent")
        from undercurrent import Client

        async with Client(**ref["connection"]) as daemon:
            sessions = await daemon.list(labels={"cm.launch": ref["id"]})
            self.assertEqual(len(sessions), 1)
            session = sessions[0]
            self.assertEqual(session.name, "Named Codex")
            self.assertEqual(session.raw["labels"]["project"], "demo")
            self.assertEqual(
                (await daemon.metadata(session))["purpose"], "qualification"
            )
            self.assertEqual(
                (await daemon.metadata(session))["cm.environment"], self.identifier
            )
            await daemon.rename(session.id, "Renamed Codex")
        pid = json.loads((self.native / "launches.jsonl").read_text().splitlines()[0])[
            "pid"
        ]
        tick = process_start(pid)
        sent = await chat.send("hello", operation_id="send")
        await chat.disconnect()
        attached = await ConversationClient(self.client.home).attach_conversation(
            chat.reference
        )
        self.handles.append(attached)
        async with asyncio.timeout(10):
            async for event in attached.events():
                if event["kind"] == "turn.completed":
                    break
        self.assertEqual(
            (await attached.turn_result(sent["result"]["turn_id"]))["status"],
            "completed",
        )
        self.assertEqual(
            (await self.client.inspect_runtime(ref))["name"], "Renamed Codex"
        )
        await ConversationClient(self.client.home).stop_startup(
            "start", operation_id="stop"
        )
        async with asyncio.timeout(10):
            while process_start(pid) == tick:
                await asyncio.sleep(0.03)
        self.assertNotEqual(process_start(pid), tick)
        from agent_manager.storage.locks import locks

        with locks(self.manager.lock_root, "environment-" + self.identifier):
            pass

    async def test_launch_from_cli_survives_cli_exit_and_default_client_recovers_owner(
        self,
    ):
        agent = (await self.client.available_agents(providers=("codex",)))[0][
            "reference"
        ]
        self.startups.append("cli")
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-m",
            "conversation_manager",
            "--home",
            str(self.client.home),
            "--runtime",
            "undercurrent",
            "--runtime-options",
            json.dumps(self.options),
            "conversation",
            "start",
            "--agent",
            json.dumps(agent),
            "--cwd",
            str(self.root),
            "--operation-id",
            "cli",
            "--process",
            json.dumps({"name": "CLI worker"}),
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        output, error = await asyncio.wait_for(process.communicate(), 30)
        self.assertEqual(process.returncode, 0, error.decode())
        row = json.loads(output)
        attached = await ConversationClient(self.client.home).attach_conversation(
            row["conversation_ref"]
        )
        self.handles.append(attached)
        self.assertFalse((await attached.state())["closed"])
        ref = row["conversation_ref"]["recovery"]["connection_ref"]["runtime_ref"]
        self.assertEqual(
            (await ConversationClient(self.client.home).inspect_runtime(ref))["name"],
            "CLI worker",
        )

    async def test_lost_daemon_reply_retains_identity_and_can_be_stopped(self):
        from undercurrent import Client as DaemonClient
        from unittest.mock import patch

        original = DaemonClient.start
        calls = []

        async def lost(client, *args, **kwargs):
            session = await original(client, *args, **kwargs)
            calls.append(session.id)
            await asyncio.sleep(0.3)
            raise OSError("lost daemon reply")

        with patch.object(DaemonClient, "start", lost):
            try:
                await self.start("lost")
            except ConversationError as error:
                self.assertTrue(error.uncertain)
        # The worker may have completed readiness independently of the lost
        # daemon response. Either outcome keeps its exact process association.
        startup = await self.client.operation("lost")
        launch_id = startup["recovery"]["launch_operation_id"]
        launched = await self.client.operation(launch_id)
        ref = launched["recovery"]["runtime_ref"]
        self.assertEqual(
            (await ConversationClient(self.client.home).inspect_runtime(ref))[
                "session_id"
            ],
            calls[0],
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.client.store.all("undercurrent-launch")), 1)

    async def test_force_stop_kills_provider_child_and_releases_guard(self):
        chat = await self.start("forced")
        ref = chat.reference["recovery"]["connection_ref"]["runtime_ref"]
        pid = json.loads((self.native / "launches.jsonl").read_text().splitlines()[0])[
            "pid"
        ]
        tick = process_start(pid)
        from undercurrent import Client as DaemonClient

        async with DaemonClient(**ref["connection"]) as daemon:
            target = (await daemon.list(labels={"cm.launch": ref["id"]}))[0]
            await daemon.stop(target.id, force=True)
        async with asyncio.timeout(10):
            while process_start(pid) == tick:
                await asyncio.sleep(0.03)
        from agent_manager.storage.locks import locks

        with locks(self.manager.lock_root, "environment-" + self.identifier):
            pass

    async def test_daemon_restart_preserves_session_identity_without_start_replay(self):
        chat = await self.start("restart")
        ref = chat.reference["recovery"]["connection_ref"]["runtime_ref"]
        before = await self.client.inspect_runtime(ref)
        await self.stop_daemon()
        await self.start_daemon()
        after = await ConversationClient(self.client.home).inspect_runtime(ref)
        self.assertEqual(after["session_id"], before["session_id"])
        from undercurrent import Client as DaemonClient

        async with DaemonClient(**ref["connection"]) as daemon:
            self.assertEqual(len(await daemon.list(labels={"cm.launch": ref["id"]})), 1)
        self.assertEqual(
            len((self.native / "launches.jsonl").read_text().splitlines()), 1
        )

    async def test_editable_config_launches_daemon_with_process_defaults(self):
        config = self.root / "conversation.toml"
        config.write_text(
            '[runtime]\nkind="undercurrent"\n[runtime.undercurrent]\nhome='
            + json.dumps(str(self.daemon_home))
            + "\nbridge="
            + json.dumps(str(BINS / "uc-python-bridge"))
            + '\n[process]\nname_prefix="configured"\n[process.labels]\nproject="default-project"\n'
        )
        self.client = ConversationClient(
            self.client.home,
            config=config,
            agent_config=self.config,
            agent_root=self.root / "agents",
        )
        chat = await self.start("config-start")
        ref = chat.reference["recovery"]["connection_ref"]["runtime_ref"]
        self.assertTrue(
            (await self.client.inspect_runtime(ref))["name"].startswith("configured-")
        )
        from undercurrent import Client as DaemonClient

        async with DaemonClient(**ref["connection"]) as daemon:
            session = (await daemon.list(labels={"cm.launch": ref["id"]}))[0]
            self.assertEqual(session.raw["labels"]["project"], "default-project")
        # Edited defaults affect new clients, never this captured runtime owner.
        config.write_text('[runtime]\nkind="standalone"\n')
        observer = ConversationClient(self.client.home, config=config)
        self.assertEqual(observer.runtime["kind"], "standalone")
        self.assertEqual(
            (await observer.inspect_runtime(ref))["session_id"], session.id
        )

    async def test_runtime_change_conflicts_without_duplicate_launch(self):
        chat = await self.start()
        agent = (await self.client.available_agents(providers=("codex",)))[0][
            "reference"
        ]
        with self.assertRaises(ConversationError) as caught:
            await ConversationClient(self.client.home).start_conversation(
                agent=agent, cwd=self.root, operation_id="start"
            )
        self.assertEqual(caught.exception.code, "conflict")
        self.assertEqual(
            len((self.native / "launches.jsonl").read_text().splitlines()), 1
        )


@unittest.skipUnless(
    ENABLED and shutil.which("opencode"),
    "Requires Undercurrent binaries and installed OpenCode",
)
class OpenCodeRuntimeTests(DaemonMixin, unittest.IsolatedAsyncioTestCase):
    start = opencode_tests.OpenCodeFrontendTests.start
    test_shared_worker_stream_and_reconnect = opencode_tests.OpenCodeFrontendTests.test_start_stream_reconnect_and_shared_worker

    async def asyncSetUp(self):
        await opencode_tests.OpenCodeFrontendTests.asyncSetUp(self)
        await self.start_daemon()
        self.client = self.new_client()
        defaults = self.root / "conversation-defaults.toml"
        defaults.write_text(
            '[process]\nname_prefix="shared"\n[process.labels]\nproject="shared-default"\n'
        )
        self.client = ConversationClient(
            self.client.home,
            config=defaults,
            agent_config=self.config,
            agent_root=self.root / "agents",
            runtime="undercurrent",
            runtime_options=self.options,
        )

    async def test_new_operation_can_choose_standalone_after_confirmed_worker_stop(
        self,
    ):
        first = await self.start("daemon-chat")
        ref = first.reference["recovery"]["connection_ref"]["runtime_ref"]
        await self.client.stop_startup("daemon-chat", operation_id="stop-daemon-worker")
        async with asyncio.timeout(10):
            while (await self.client.inspect_runtime(ref))["state"] not in (
                "stopped",
                "exited",
                "failed",
            ):
                await asyncio.sleep(0.03)
        self.client = ConversationClient(
            self.client.home, agent_config=self.config, agent_root=self.root / "agents"
        )
        second = await self.start("standalone-chat")
        self.assertEqual(
            second.reference["recovery"]["connection_ref"]["runtime_ref"]["owner"],
            "conversation-manager-standalone",
        )
        with self.assertRaises(ConversationError):
            await self.start("daemon-chat")

    async def asyncTearDown(self):
        try:
            # Keep daemon and provider model alive until all conversations stop.
            await opencode_tests.OpenCodeFrontendTests.asyncTearDown(self)
        finally:
            await self.stop_daemon()
