"""A retained task owns SDK entry and exit, independently of IPC callers."""

import asyncio

from ..models import ConversationError


class SdkSession:
    def __init__(self, driver):
        self.driver = driver
        self.queue = asyncio.Queue()
        self.ready = asyncio.get_running_loop().create_future()
        self.prompt_task = None
        self.closed = False
        self.close_task = None
        self.owner = asyncio.create_task(self.own())

    async def own(self):
        current = None
        try:
            identity = await self.driver.start()
            self.ready.set_result(identity)
            while True:
                method, args, future = await self.queue.get()
                current = future
                if method == "close":
                    if self.prompt_task and not self.prompt_task.done():
                        await self.driver.cancel()
                        try:
                            await asyncio.wait_for(asyncio.shield(self.prompt_task), 5)
                        except (TimeoutError, Exception):
                            self.prompt_task.cancel()
                            await asyncio.gather(
                                self.prompt_task, return_exceptions=True
                            )
                    future.set_result(None)
                    break
                if method == "prompt":
                    if self.prompt_task and not self.prompt_task.done():
                        future.set_exception(
                            ConversationError("busy", "SDK session has an active turn")
                        )
                        continue
                    self.prompt_task = asyncio.create_task(
                        self.call(method, args, future)
                    )
                    # Enter the prompt before consuming an already queued cancel.
                    # Drivers can then retain cancellation during initial I/O.
                    await asyncio.sleep(0)
                else:
                    await self.call(method, args, future)
        except BaseException as error:
            if not self.ready.done():
                self.ready.set_exception(error)
            if current is not None and not current.done():
                current.set_exception(
                    ConversationError(
                        "transport", "SDK session owner stopped", uncertain=True
                    )
                )
            while not self.queue.empty():
                _, _, future = self.queue.get_nowait()
                if not future.done():
                    future.set_exception(
                        ConversationError(
                            "transport", "SDK session owner stopped", uncertain=True
                        )
                    )
            raise
        finally:
            self.closed = True
            if self.prompt_task and not self.prompt_task.done():
                self.prompt_task.cancel()
                await asyncio.gather(self.prompt_task, return_exceptions=True)
            await self.driver.close()

    async def call(self, method, args, future):
        try:
            value = await getattr(self.driver, method)(*args)
        except BaseException as error:
            if not future.done():
                future.set_exception(
                    ConversationError(
                        "transport",
                        "SDK operation was interrupted without a provider result",
                        uncertain=True,
                    )
                    if isinstance(error, asyncio.CancelledError)
                    else error
                )
        else:
            if not future.done():
                future.set_result(value)

    async def submit(self, method, *args):
        if self.closed:
            raise ConversationError("transport", "SDK session is closed")
        future = asyncio.get_running_loop().create_future()
        await self.queue.put((method, args, future))
        return await asyncio.shield(future)

    async def close(self):
        if self.close_task is None:
            self.close_task = asyncio.create_task(self._close())
        await asyncio.shield(self.close_task)

    async def _close(self):
        if not self.closed:
            try:
                await self.submit("close")
            except ConversationError:
                pass
        await asyncio.gather(self.owner, return_exceptions=True)
