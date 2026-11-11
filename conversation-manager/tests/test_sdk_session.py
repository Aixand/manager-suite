"""SDK context ownership must survive failed cancellation and concurrent detach."""

import asyncio
import unittest

from conversation_manager import ConversationError
from conversation_manager.adapters.sdk_session import SdkSession


class Driver:
    def __init__(self):
        self.started = asyncio.Event()
        self.close_count = 0

    async def start(self):
        self.owner = asyncio.current_task()
        return "identity"

    async def prompt(self, text):
        self.started.set()
        await asyncio.Future()

    async def cancel(self):
        raise ConnectionError("lost cancellation delivery")

    async def close(self):
        assert asyncio.current_task() is self.owner
        self.close_count += 1


class SdkSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_cancellation_cannot_hang_shutdown_or_claim_completion(self):
        driver = Driver()
        actor = SdkSession(driver)
        self.assertEqual(await actor.ready, "identity")
        pending = asyncio.create_task(actor.submit("prompt", "hold"))
        await driver.started.wait()
        async with asyncio.timeout(1):
            await asyncio.gather(actor.close(), actor.close())
            with self.assertRaises(ConversationError) as outcome:
                await pending
        self.assertTrue(outcome.exception.uncertain)
        self.assertEqual(driver.close_count, 1)
        self.assertTrue(actor.closed)
