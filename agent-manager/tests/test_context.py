import asyncio
import json
import os
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch
import uuid

from agent_manager import Client, Context, Error, application_context, project_context
from agent_manager.runtime.context import attributes, session_context
from agent_manager.runtime.runs import finish_run, read_run, update_run
from tests import support as sdk_fixtures
from tests import test_thread_spawn as thread_fixtures


class ContextTests(unittest.TestCase):
    def test_copying_independent_layers_and_selective_inheritance(self):
        labels, metadata = {"app": "pathspec"}, {"private": "original"}
        base = Context(labels, metadata)
        labels["app"] = "changed"
        metadata["private"] = "changed"
        first = base.resolve(labels={"workflow.run_id": "one"}, metadata={"private": "first"})
        second = base.resolve(labels={"workflow.run_id": "two"})
        first.labels["app"] = "changed"
        self.assertEqual(second.labels, {"app": "pathspec", "workflow.run_id": "two"})
        self.assertEqual(second.metadata, {"private": "original"})
        fresh = first.resolve(labels={"role": "reviewer"}, inherit_labels=False)
        self.assertEqual(fresh.labels, {"role": "reviewer"})
        self.assertEqual(fresh.metadata, {"private": "first"})
        self.assertNotIn("original", repr(base))

    def test_limits_are_utf8_bytes_and_errors_do_not_echo_values(self):
        for kind, maximum in (("labels", 256), ("metadata", 4096)):
            self.assertEqual(attributes({"valid": "é" * (maximum // 2)}, kind)["valid"], "é" * (maximum // 2))
            for value in ("é" * (maximum // 2 + 1), "private\nsecret", {"nested": "value"}):
                with self.assertRaises(Error) as caught:
                    attributes({"valid": value}, kind)
                self.assertNotIn("secret", str(caught.exception))
        for invalid in ({"agent_manager.run_id": "override"}, {"": "empty"}, {str(i): "v" for i in range(65)}):
            with self.assertRaises(Error):
                Context(invalid)

    def test_project_identity_uses_canonical_original_root(self):
        root = Path.cwd()
        self.assertEqual(project_context(root), project_context(root / ".." / root.name))
        self.assertEqual(project_context(root, project_id="shared")["labels"]["project.id"], "shared")
        self.assertEqual(project_context(root)["metadata"]["project.root"], str(root.resolve()))

    def test_application_handoff_preserves_origin_task_context_and_owns_identity(self):
        source = {"labels": {"app": "example-app", "origin.app": "external", "workflow.run_id": "one",
                             "project.id": "shared", "project.name": "Shared project"},
                  "metadata": {"project.root": str(Path.cwd()), "suite.source": "retained"}}
        result = application_context("pathspec", "planning", project=Path.cwd(), inherited=source,
            labels={"app": "wrong", "feature": "wrong", "origin.app": "wrong", "role": "planner"}, session_kind="agent")
        self.assertEqual(result["labels"]["app"], "pathspec")
        self.assertEqual(result["labels"]["feature"], "planning")
        self.assertEqual(result["labels"]["origin.app"], "external")
        self.assertEqual(result["labels"]["workflow.run_id"], "one")
        self.assertEqual(result["labels"]["project.id"], "shared")
        self.assertEqual(result["metadata"]["suite.source"], "retained")
        changed = application_context("pathspec", "planning", project=Path.cwd() / "another", inherited=source)
        self.assertNotEqual(changed["labels"]["project.id"], "shared")

    def test_process_classification_and_relationships_do_not_leak_from_parent(self):
        parent = Context({"session.kind": "server", "service.name": "codex-app-server"},
                         {"session.thread_id": "old", "source": "retained"})
        result = session_context(parent, "agent", thread_id="thread")
        self.assertEqual(result.labels, {"session.kind": "agent"})
        self.assertEqual(result.metadata, {"session.thread_id": "thread", "source": "retained"})
        self.assertEqual(parent.labels["session.kind"], "server")
        with self.assertRaises(Error):
            application_context("pathspec", "runner", session_kind="server")


class ManagedContextTests(unittest.IsolatedAsyncioTestCase):
    setUp = sdk_fixtures.ManagedFixture.setUp
    create = sdk_fixtures.ManagedFixture.create
    client = sdk_fixtures.ManagedFixture.client
    launch = sdk_fixtures.ManagedFixture.launch

    async def test_new_defaults_do_not_relabel_or_relaunch_an_existing_keyed_run(self):
        self.create()
        client, key = self.client(), str(uuid.uuid4())
        with patch("agent_manager.interfaces.sdk.session_context", side_effect=lambda context, *_args, **_kwargs: context):
            original = await self.launch(client, idempotency_key=key)
        before = read_run(self.manager, original.run_id)
        with patch.object(client, "reconcile", new=AsyncMock()), patch("agent_manager.interfaces.sdk.launch_worker", side_effect=AssertionError("must reuse existing launch")):
            recovered = await client.spawn(profile="work", idempotency_key=key)
        self.assertEqual(recovered.run_id, original.run_id)
        self.assertEqual(read_run(self.manager, original.run_id), before)
        fresh = await self.launch(client)
        self.assertEqual(fresh.labels["session.kind"], "agent")

    async def test_concurrent_views_are_independent_and_persist_before_launch(self):
        self.create()
        base = self.client().with_context(labels={"app": "pathspec"}, metadata={"project.root": "original"})
        first = base.with_context(labels={"workflow.run_id": "one"})
        second = base.with_context(labels={"workflow.run_id": "two"})
        def accepted(manager, run_id):
            update_run(manager, run_id, session_id="session-" + run_id, status="running")
        # One patch owns both concurrent preparations; overlapping helper
        # patches can restore the real worker while the other task is running.
        with patch("agent_manager.interfaces.sdk.launch_worker", side_effect=accepted):
            one, two = await asyncio.gather(first.spawn(profile="work", labels={"role": "implementer"}),
                                            second.spawn(profile="work", labels={"role": "reviewer"}))
        self.assertEqual(one.labels["workflow.run_id"], "one")
        self.assertEqual(two.labels["workflow.run_id"], "two")
        self.assertNotIn("workflow.run_id", base.context["labels"])
        self.assertEqual(read_run(self.manager, one.run_id)["launch"]["metadata"], {"project.root": "original"})
        self.assertEqual(await base.metadata(one.run_id), {"project.root": "original"})
        base.runtime.close = AsyncMock()
        await first.close()
        base.runtime.close.assert_not_awaited()

    async def test_provider_environment_client_scope_and_spawn_precedence(self):
        self.create()
        self.config.write_text(self.config.read_text() + '\n[providers.test.labels]\nsource="config"\nteam="aixand"\n')
        with patch.dict(os.environ, AGENT_MANAGER_LABELS='{"source":"environment"}', AGENT_MANAGER_METADATA='{"private":"environment"}'):
            client = Client("test", config=self.config, root=self.base / "homes", runtime=self.client().runtime,
                            labels={"source": "client"})
        scoped = client.with_context(labels={"source": "scope"})
        agent = await self.launch(scoped, labels={"source": "spawn"})
        self.assertEqual(agent.labels["source"], "spawn")
        self.assertEqual(agent.labels["team"], "aixand")
        self.assertEqual(await scoped.metadata(agent.run_id), {"private": "environment"})

    async def test_filtered_history_applies_limit_after_selection_and_hides_metadata(self):
        self.create()
        client = self.client()
        wanted = await self.launch(client, labels={"app": "pathspec"}, metadata={"private": "private-context-token"})
        finish_run(self.manager, wanted.run_id, status="finished")
        await self.launch(client, labels={"app": "suite"})
        rows = await client.runs(labels={"app": "pathspec"}, limit=1)
        self.assertEqual([row["run_id"] for row in rows], [wanted.run_id])
        self.assertNotIn("private-context-token", json.dumps(rows))
        self.assertEqual(await client.metadata(wanted.run_id), {"private": "private-context-token"})
        self.assertEqual(await client.runs(labels={"app": "pathspec", "role": "missing"}), [])

    async def test_idempotency_covers_both_maps_and_preserves_no_context_retries(self):
        self.create()
        key = str(uuid.uuid4())
        first = await self.launch(idempotency_key=key, labels={"app": "pathspec"}, metadata={"digest": "one"})
        second = await self.launch(idempotency_key=key, labels={"app": "pathspec"}, metadata={"digest": "one"})
        self.assertEqual(first.run_id, second.run_id)
        for options in ({"labels": {"app": "suite"}, "metadata": {"digest": "one"}},
                        {"labels": {"app": "pathspec"}, "metadata": {"digest": "two"}}):
            with self.assertRaisesRegex(Error, "different launch arguments"):
                await self.launch(idempotency_key=key, **options)
        legacy = str(uuid.uuid4())
        self.assertEqual((await self.launch(idempotency_key=legacy)).run_id,
                         (await self.launch(idempotency_key=legacy)).run_id)

    async def test_invalid_or_unsupported_context_has_no_launch_effect(self):
        self.create()
        client = self.client()
        client.runtime.connect = AsyncMock()
        async def old_start(command):
            pass
        client.runtime.start = old_start
        before = self.manager.state()
        for options in ({"metadata": {"private": "value"}}, {"labels": {str(i): "v" for i in range(63)}},
                        {"labels": {"agent_manager.run_id": "override"}}):
            with self.assertRaises(Error):
                await self.launch(client, **options)
        client.runtime.connect.assert_not_awaited()
        self.assertEqual(self.manager.state(), before)


class ThreadContextTests(unittest.IsolatedAsyncioTestCase):
    setUp = thread_fixtures.ThreadSpawnTests.setUp
    saved_thread = thread_fixtures.ThreadSpawnTests.saved_thread
    spawn = thread_fixtures.ThreadSpawnTests.spawn

    async def test_threads_keep_separate_context_and_terminal_restores_saved_context(self):
        from agent_manager.runtime.context import ThreadStore
        saved = Context({'app': 'suite', 'workflow.run_id': 'one'}, {'private': 'thread-context'})
        ThreadStore(self.manager).save('work', self.thread_id, saved, cwd=self.root)
        self.assertNotIn('thread-context', json.dumps(await self.client.threads(labels={'workflow.run_id': 'one'})))
        self.assertEqual(await self.client.thread_metadata('work', self.thread_id), {'private': 'thread-context'})
        self.saved_thread()
        self.client = self.client.with_context(labels={"workflow.run_id": "different"})
        terminal = await self.spawn(thread_id=self.thread_id, labels={"feature": "terminal"})
        self.assertEqual(terminal.labels["workflow.run_id"], "one")
        self.assertEqual(await self.client.metadata(terminal.run_id), {"private": "thread-context", "session.thread_id": self.thread_id})
