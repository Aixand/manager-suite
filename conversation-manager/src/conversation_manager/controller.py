"""Native protocol-controller lifecycle over a supplied process owner."""
from dataclasses import asdict, dataclass
from contextlib import contextmanager
import sys
import uuid

from aixand_filesystem import lock_file, private_path
from .models import digest, ConversationError
from .store import Store


@dataclass(frozen=True)
class ControllerSpec:
    epoch: str
    upstream: str
    frontend: str
    control: str
    cwd: str
    name: str
    native_id: str

    def __post_init__(self):
        if any(not isinstance(value, str) or not value or '\0' in value for value in asdict(self).values()):
            raise ConversationError('validation', 'Controller specification requires nonempty strings')


class NativeController:
    schema = 'conversation-manager/native-controller-1'
    terminal = frozenset(('stopped', 'exited', 'failed', 'missing'))

    def __init__(self, home=None): self.store = Store(home)

    @contextmanager
    def _lease(self, key):
        root = self.store.home / 'controller-locks'
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (root / (key + '.lock')).open('a') as stream:
            private_path(stream.name)
            try: lock_file(stream, blocking=False)
            except BlockingIOError:
                raise ConversationError('busy', 'Controller lifecycle is already being advanced') from None
            yield

    def _load(self, spec, owner, initial):
        key = digest([spec.epoch, owner.identity])[7:]
        record = self.store.get('native-controller', key)
        if record is None:
            seed = initial or {}
            state = dict(attempt=seed.get('attempt', 0), phase=seed.get('phase', 'prepared'), process_id=seed.get('process_id'))
            record = dict(spec=asdict(spec), owner=owner.identity, state=state)
        elif ({key: value for key, value in record['spec'].items() if key != 'name'} !=
              {key: value for key, value in asdict(spec).items() if key != 'name'} or record['owner'] != owner.identity):
            raise ConversationError('conflict', 'Native controller specification changed')
        record['spec']['name'] = spec.name
        return key, record

    async def _checkpoint(self, key, record, callback):
        self.store.put('native-controller', key, record)
        if callback is not None: await callback(dict(record['state']))

    async def ensure(self, spec, owner, *, initial_state=None, on_state=None):
        key = digest([spec.epoch, owner.identity])[7:]
        with self._lease(key):
            key, record = self._load(spec, owner, initial_state)
            state = record['state']
            if await owner.inspect(spec.native_id) in self.terminal:
                return dict(state)
            if state['process_id']:
                if await owner.inspect(state['process_id']) not in self.terminal:
                    await self._checkpoint(key, record, on_state)
                    return dict(state)
                state.update(attempt=state['attempt'] + 1, phase='prepared', process_id=None)
                await self._checkpoint(key, record, on_state)
            matches = await owner.find(spec.epoch, state['attempt'])
            if len(matches) > 1:
                raise ConversationError('uncertain', 'Multiple controller processes match this attempt', uncertain=True)
            if matches:
                process_id = matches[0]
            elif state['phase'] == 'prepared':
                state['phase'] = 'submitting'
                await self._checkpoint(key, record, on_state)
                command = [sys.executable, '-m', 'conversation_manager.native_gateway',
                    '--upstream', spec.upstream, '--socket', spec.frontend, '--control', spec.control, '--epoch', spec.epoch]
                try: namespace = uuid.UUID(spec.epoch)
                except ValueError: namespace = uuid.uuid5(uuid.NAMESPACE_URL, spec.epoch)
                operation = uuid.uuid5(namespace, 'native-controller:' + str(state['attempt'])).hex
                process_id = await owner.start(command, cwd=spec.cwd,
                    name=spec.name + '-controller' + (f'-{state["attempt"] + 1:02d}' if state['attempt'] else ''),
                    operation_id=operation, epoch=spec.epoch, attempt=state['attempt'])
            else:
                raise ConversationError('uncertain', 'App-server controller launch is unconfirmed; its native process was retained', uncertain=True)
            state.update(process_id=process_id, phase='running')
            await self._checkpoint(key, record, on_state)
            return dict(state)

    async def retire(self, spec, owner, *, initial_state=None, on_state=None):
        key = digest([spec.epoch, owner.identity])[7:]
        with self._lease(key):
            key, record = self._load(spec, owner, initial_state)
            state = record['state']
            if state['phase'] == 'submitting' and not state['process_id']:
                matches = await owner.find(spec.epoch, state['attempt'])
                if len(matches) != 1: return False
                state.update(process_id=matches[0], phase='running')
                await self._checkpoint(key, record, on_state)
            target = state['process_id']
            if target is None: return state['phase'] in ('prepared', 'retired')
            try:
                if await owner.inspect(target) not in self.terminal:
                    if not await owner.stop(target): return False
                if await owner.inspect(target) not in self.terminal: return False
            except Exception: return False
            state['phase'] = 'retired'
            await self._checkpoint(key, record, on_state)
            return True
