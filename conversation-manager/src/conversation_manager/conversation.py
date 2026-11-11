"""A conversation bound to an exact backend connection and native identity."""
from copy import deepcopy


class Conversation:
    def __init__(self, connection, reference, *, creation_receipt=None):
        self.connection = connection
        self._reference = deepcopy(reference)
        self.creation_receipt = deepcopy(creation_receipt)
        self.startup_receipt = None

    @property
    def reference(self):
        return deepcopy(self._reference)

    async def disconnect(self):
        """Detach this frontend; leave the retained conversation running."""
        await self.connection.disconnect()

    async def reconnect(self):
        await self.connection.reconnect()
        await self.connection.session_state(self.reference)
        return self

    async def capabilities(self):
        return await self.connection.capabilities()

    async def send(self, content, *, operation_id):
        return await self.connection.send(self.reference, content, operation_id=operation_id)

    async def state(self):
        return await self.connection.session_state(self.reference)

    async def history(self, *, cursor=0, limit=100):
        return await self.connection.history_page(self.reference, cursor=cursor, limit=limit)

    def events(self, *, after_sequence=None):
        return self.connection.events(self.reference, after_sequence=after_sequence)

    async def turn_result(self, turn_id):
        return await self.connection.turn_result(self.reference, turn_id)

    async def cancel(self, turn_id, *, operation_id):
        return await self.connection.cancel(self.reference, turn_id, operation_id=operation_id)

    async def queue_state(self):
        return await self.connection.queue_state(self.reference)

    async def enqueue(self, text, *, mode='after_turn', expected_turn_id, operation_id):
        return await self.connection.update_queue(self.reference, 'enqueue', text=text, mode=mode,
            expected_turn_id=expected_turn_id, operation_id=operation_id)

    async def inspect_queued(self, queue_id):
        return await self.connection.client.inspect_queued(self.reference, queue_id)

    async def reconcile_queued(self, queue_id, *, operation_id):
        return await self.connection.update_queue(self.reference, 'reconcile', queue_id=queue_id, operation_id=operation_id)

    async def edit_queued(self, queue_id, text, *, operation_id, expected_revision=None):
        return await self.connection.update_queue(self.reference, 'edit', queue_id=queue_id, text=text, operation_id=operation_id,
            **({'expected_revision': expected_revision} if expected_revision is not None else {}))

    async def remove_queued(self, queue_id, *, operation_id, expected_revision=None):
        return await self.connection.update_queue(self.reference, 'remove', queue_id=queue_id, operation_id=operation_id,
            **({'expected_revision': expected_revision} if expected_revision is not None else {}))

    async def pause_queue(self, *, operation_id):
        return await self.connection.update_queue(self.reference, 'pause', operation_id=operation_id)

    async def resume_queue(self, *, operation_id):
        return await self.connection.update_queue(self.reference, 'resume', operation_id=operation_id)

    async def steer(self, text, *, expected_turn_id, operation_id):
        return await self.connection.steer(self.reference, text,
            expected_turn_id=expected_turn_id, operation_id=operation_id)

    async def pending_requests(self):
        return await self.connection.pending_requests(self.reference)

    async def respond(self, request_id, response, *, operation_id):
        from .models import ConversationError
        pending = await self.pending_requests()
        if not any(item['request_id'] == request_id for item in pending):
            raise ConversationError('conflict', 'Request does not belong to this conversation or is no longer pending')
        return await self.connection.respond(request_id, response, operation_id=operation_id)

    async def commands(self):
        return await self.connection.commands(self.reference)

    async def list_slash_commands(self, *, available_only=False):
        """List discovered commands and their current supported execution paths."""
        return await self.connection.list_slash_commands(self.reference, available_only=available_only)

    async def command(self, name, arguments='', *, operation_id):
        return await self.connection.command(self.reference, name, arguments, operation_id=operation_id)

    async def configuration(self):
        return await self.connection.configuration(self.reference)

    async def set_config(self, name, value, *, operation_id):
        return await self.connection.set_config(self.reference, name, value, operation_id=operation_id)

    async def mcp_target(self):
        """Describe this conversation's retained MCP selection and exact runtime."""
        return await self.connection.mcp_target(self.reference)

    async def close(self, *, operation_id):
        """Close this conversation; backend support is capability checked."""
        return await self.connection.close_session(self.reference, operation_id=operation_id)
