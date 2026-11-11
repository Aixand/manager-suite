"""Public Agent Manager target integration, without private launch APIs."""
from copy import deepcopy
from .models import ConversationError


class AgentEnvironments:
    """Resolve targets with a synchronous public agent_manager.Manager.

    Targets are observations, not authentication grants or retirement leases.
    This integration deliberately does not infer launch credentials from a home.
    """
    def __init__(self, provider='codex', *, config=None, root=None):
        from pathlib import Path
        self.identity = dict(provider=provider,
            config=str(Path(config).expanduser().resolve()) if config is not None else None,
            root=str(Path(root).expanduser().resolve()) if root is not None else None)
        try:
            from agent_manager import Manager
        except ImportError as exc:
            raise ConversationError('unsupported', 'Install aixand-conversation-manager[agents] for environment discovery') from exc
        self._manager = Manager(provider, config=self.identity['config'], root=self.identity['root'])

    def list(self):
        return [self._validate(target) for target in self._manager.agent_targets()]

    def resolve(self, environment_id):
        return self._validate(self._manager.resolve_agent_target(environment_id))

    def prepare_codex(self, environment_id):
        return self.prepare_native(environment_id)

    def prepare_native(self, environment_id):
        """Capture a portable intent; the worker reacquires and revalidates it."""
        if not hasattr(self._manager, 'lease_native_environment'):
            raise ConversationError('unsupported', 'Upgrade Agent Manager to a build with lease_native_environment')
        with self._manager.lease_native_environment(environment_id) as lease:
            if not callable(getattr(lease, 'subprocess_options', None)):
                raise ConversationError('unsupported', 'Upgrade Agent Manager for native subprocess lease options')
            return deepcopy(lease.descriptor)

    def lease_codex(self, descriptor):
        return self.lease_native(descriptor)

    def lease_native(self, descriptor):
        return self._manager.lease_native_environment(
            descriptor['target']['environment_id'], expected_revision=descriptor['revision'])

    def _validate(self, target):
        if (not isinstance(target, dict) or target.get('schema') != 'agent-manager/target-1'
                or target.get('provider') != self._manager.provider
                or any(not isinstance(target.get(key), str) or not target[key]
                       for key in ('environment_id', 'registry', 'home'))):
            raise ConversationError('validation', 'Agent Manager returned an incompatible environment target')
        return deepcopy(target)
