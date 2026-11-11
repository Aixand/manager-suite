"""Exact execution targeting and thin Undercurrent operations for SDK handles."""
from contextlib import aclosing
from pathlib import Path
import time

from agent_manager.errors import Error
from agent_manager.interfaces.contracts import ExecutionReference, ExecutionState, ExecutionUpdate


def reference(manager, record):
    if record.get('runtime') != 'undercurrent' or not record.get('session_id'):
        raise Error('An execution handle requires an acknowledged Undercurrent execution')
    home = record.get('connection', {}).get('home')
    if not home: raise Error('The original Undercurrent home is unknown')
    return ExecutionReference(manager.provider, str(manager.state_path.resolve()), record['run_id'],
        str(Path(home).expanduser().resolve()), record['session_id'])


def validate(client, record, expected=None):
    current = reference(client.manager, record)
    home = client.runtime.connection_options.get('home')
    if not home or str(Path(home).expanduser().resolve()) != current.runtime_home:
        raise Error('Execution belongs to a different Undercurrent home; reconnect using its original runtime')
    if expected is not None and current != expected:
        raise Error('Execution reference differs from this recorded execution')
    return current


def state(ref, snapshot):
    if snapshot.id != ref.session_id: raise Error('Undercurrent observed a different session identity')
    return ExecutionState(ref, snapshot.lifecycle, snapshot.execution, snapshot.interaction,
        snapshot.constraint, snapshot.health, snapshot.revision, time.time(), snapshot)


class Operations:
    """Mixed into the immutable Agent identity; binding is private and nonserialized."""
    def _bind(self, client, ref, selection=None):
        object.__setattr__(self, '_client', client)
        object.__setattr__(self, '_reference', ref)
        object.__setattr__(self, '_selection', selection)
        return self

    @property
    def reference(self):
        if not hasattr(self, '_reference'): raise Error('Recover this execution through Client.get before controlling it')
        return self._reference

    @property
    def selection(self): return getattr(self, '_selection', None)

    @property
    def runtime_ref(self):
        return dict(owner='undercurrent', id=self.reference.session_id, home=self.reference.runtime_home)

    async def _target(self):
        ref = self.reference
        recovered = await self._client.get(ref)
        await self._client.runtime.connect()
        return recovered.session_id

    async def _call(self, method, *args, **options):
        target = await self._target()
        return await getattr(self._client.runtime, method)(target, *args, **options)

    async def inspect(self): return state(self.reference, await self._call('inspect'))
    async def send_input(self, text, *, submit=True, delivery_id=None, expected_revision=None,
                         preserve_observation=False, paste=None):
        return await self._call('send_input', text, submit=submit, delivery_id=delivery_id,
            expected_revision=expected_revision, preserve_observation=preserve_observation, paste=paste)
    async def send_key(self, key, *, delivery_id=None):
        return await self._call('send_key', key, delivery_id=delivery_id)
    async def read_output(self, *, from_offset=0, max_bytes=65536):
        return await self._call('read_output', from_offset=from_offset, max_bytes=max_bytes)
    async def read_screen(self): return await self._call('read_screen')
    async def stop(self, *, force=False, graceful_only=False, expected_revision=None):
        return state(self.reference, await self._call('stop', force=force,
            graceful_only=graceful_only, expected_revision=expected_revision))
    async def wait(self, *, timeout=300.0, after_revision=None, **conditions):
        return state(self.reference, await self._call('wait', timeout=timeout,
            after_revision=after_revision, **conditions))
    async def watch(self):
        target = await self._target()
        async with aclosing(self._client.runtime.watch(target)) as stream:
            async for update in stream:
                yield ExecutionUpdate(state(self.reference, update.session), update.resynchronized)
    async def events(self, *, after_sequence=None):
        target = await self._target()
        async with aclosing(self._client.runtime.events(target, after_sequence=after_sequence)) as stream:
            async for item in stream: yield item
