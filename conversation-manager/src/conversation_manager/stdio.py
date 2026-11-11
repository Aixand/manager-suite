"""Bounded JSONL transport for a worker-owned native process."""
import asyncio


class MessageTooLarge(Exception):
    pass


class StdioTransport:
    def __init__(self, process, *, max_message_bytes):
        self.process = process
        self.max_message_bytes = max_message_bytes
        self._writes = asyncio.Lock()
        self.closed = False

    async def send(self, message):
        data = message.encode('utf-8')
        if len(data) > self.max_message_bytes:
            raise MessageTooLarge()
        async with self._writes:
            if self.closed:
                raise ConnectionError('Native input is closed')
            self.process.stdin.write(data + b'\n')
            await self.process.stdin.drain()

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            raw = await self.process.stdout.readline()
        except (ValueError, asyncio.LimitOverrunError) as error:
            raise MessageTooLarge() from error
        if not raw:
            raise StopAsyncIteration
        if not raw.endswith(b'\n'):
            raise ConnectionError('Native output ended inside a message')
        if len(raw) - 1 > self.max_message_bytes:
            raise MessageTooLarge()
        return raw.decode('utf-8')

    async def close(self):
        self.closed = True
        self.process.stdin.close()
