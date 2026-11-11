"""Durable owner reconciliation, including missing replies and foreign sessions."""

import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from conversation_manager import ConversationError
from conversation_manager.runtime import process_options
from conversation_manager.undercurrent_owner import UndercurrentOwner


class Daemon:
    def __init__(self):
        self.starts = 0
        self.rows = {}
        self.lose = False
        self.empty = False
        self.stops = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def start(self, command, **options):
        self.starts += 1
        self.options = options
        self.rows["stable-id"] = (
            SimpleNamespace(id="stable-id", name=options["name"], lifecycle="running"),
            options["metadata"],
        )
        if self.lose:
            raise OSError("lost reply")
        return self.rows["stable-id"][0]

    async def list(self, **kwargs):
        return [] if self.empty else [r[0] for r in self.rows.values()]

    async def inspect(self, identifier):
        return self.rows[identifier][0]

    async def metadata(self, identifier):
        return self.rows[identifier][1]

    async def stop(self, identifier):
        self.stops += 1
        self.rows[identifier][0].lifecycle = "stopped"


class OwnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.owner = UndercurrentOwner(
            self.tmp.name,
            {"home": "/fixture/daemon"},
            process={
                "name": "Worker",
                "labels": {"project": "test"},
                "metadata": {"purpose": "testing"},
            },
        )
        self.daemon = Daemon()
        self.patch = patch.object(UndercurrentOwner, "client", return_value=self.daemon)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    async def test_lost_start_reply_reconciles_original_id_without_second_dispatch(
        self,
    ):
        self.daemon.lose = True
        with self.assertRaises(ConversationError) as caught:
            await self.owner.start(
                ["/python", "worker"], cwd="/work", operation_id="launch"
            )
        ref = caught.exception.recovery["runtime_ref"]
        second = UndercurrentOwner(
            self.tmp.name, {"home": "/fixture/daemon"}, process=self.owner.process
        )
        self.assertEqual((await second.inspect(ref))["session_id"], "stable-id")
        self.assertEqual(
            await second.start(
                ["/python", "worker"], cwd="/work", operation_id="launch"
            ),
            ref,
        )
        self.assertEqual(self.daemon.starts, 1)
        self.assertFalse(self.daemon.options["detection"])
        self.assertEqual(self.daemon.options["metadata"]["purpose"], "testing")

    async def test_empty_lookup_or_foreign_marker_cannot_authorize_new_start(self):
        self.daemon.lose = True
        with self.assertRaises(ConversationError) as caught:
            await self.owner.start(["/python"], cwd="/work", operation_id="launch")
        ref = caught.exception.recovery["runtime_ref"]
        self.daemon.empty = True
        with self.assertRaises(ConversationError):
            await self.owner.inspect(ref)
        with self.assertRaises(ConversationError):
            await self.owner.start(["/python"], cwd="/work", operation_id="launch")
        self.daemon.empty = False
        self.daemon.rows["stable-id"][1]["cm.fingerprint"] = "foreign"
        with self.assertRaises(ConversationError):
            await self.owner.inspect(ref)
        self.assertEqual(self.daemon.starts, 1)

    async def test_stop_uses_saved_id_after_rename_and_never_stops_foreign_owner(self):
        ref = await self.owner.start(["/python"], cwd="/work", operation_id="launch")
        self.daemon.rows["stable-id"][0].name = "Renamed"
        await self.owner.stop(ref, operation_id="stop")
        self.assertEqual((await self.owner.inspect(ref))["state"], "stopped")
        with self.assertRaises(ConversationError):
            await self.owner.stop(
                {**ref, "connection": {"home": "/foreign"}}, operation_id="foreign"
            )
        self.assertEqual(self.daemon.stops, 1)

    async def test_process_attributes_reject_reserved_keys_and_secret_fields(self):
        for options in (
            {"labels": {"cm.launch": "foreign"}},
            {"metadata": {"api_key": "secret"}},
            {"metadata": {"bad": "new\nline"}},
            {"name": ""},
            [],
            {"command": "shell"},
        ):
            with self.subTest(options=options), self.assertRaises(ConversationError):
                process_options(options)
