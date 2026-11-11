"""Lock the selected profile identity and home for a launch intent."""
import contextlib
import contextvars
from dataclasses import dataclass
from pathlib import Path
from agent_manager.errors import Error
from agent_manager.storage.locks import locks, shared_environment_keys
from agent_manager.environments.homes import base_home, using_home
from agent_manager.profiles.identities import resolve
from agent_manager.runtime.continuation.base import NativeAdapter
from agent_manager.runtime.continuation.contracts import ContinuationPlan


@dataclass
class PreparedHome:
    home: Path
    environment_id: str
    _release: object
    adapter: NativeAdapter
    source_session: dict | None = None
    history_view: object = None
    continuation_plan: ContinuationPlan | None = None
    admission: object = None

    @property
    def provenance(self):
        if self.source_session is None: return {}
        from agent_manager.runtime.continuation.coordinator import provenance
        result = provenance(self.adapter, self.source_session, self.history_view, self.continuation_plan)
        if self.admission is not None:
            result['native_writer_guard'] = self.admission.to_dict()
        return result

    def claim(self, manager, state, profile):
        row = state["environments"].get(self.environment_id)
        if not row or resolve(state, profile).environment_id != self.environment_id or Path(row["home"]).resolve() != self.home.resolve():
            raise Error("Execution profile changed during launch; retry")
        if self.source_session is not None:
            from agent_manager.runtime.continuation.coordinator import claim
            claim(manager, state, self)

    def release(self): self._release()


@contextlib.contextmanager
def launch_home(manager, profile, *, args=(), cwd=None, env=None, thread_id=None, pinned=None, source_session=None, continuation_plan=None):
    profile = manager.resolve_profile(profile)
    home = base_home(manager, profile)
    if pinned is not None and Path(pinned).resolve() != home.resolve():
        raise Error("Native continuation across profile homes is not qualified for this provider")
    with contextlib.ExitStack() as stack:
        lock = contextlib.ExitStack()
        lock.enter_context(locks(manager.lock_root, "environment-" + profile.environment_id, wait=True,
                                 shared_keys=shared_environment_keys(manager, profile.environment_id)))
        stack.callback(lock.close)
        view = None
        if source_session is not None:
            from agent_manager.runtime.continuation.coordinator import prepare_history
            view = prepare_history(manager, profile, source_session, args=args, cwd=cwd,
                                   thread_id=thread_id, plan=continuation_plan)
        from agent_manager.runtime.continuation.registry import for_manager
        plan = PreparedHome(home, profile.environment_id, lock.close, for_manager(manager), source_session, view, continuation_plan)
        if source_session is not None:
            plan.admission = plan.adapter.prepare_admission(manager, home, source_session)
        token = _launch.set((str(manager.root.resolve()), plan))
        stack.callback(_launch.reset, token)
        with using_home(manager, profile, home):
            yield plan


_launch = contextvars.ContextVar("agent_manager_launch_home", default=None)


def current_launch(manager, home):
    selected = _launch.get()
    if selected is not None and selected[0] == str(manager.root.resolve()) and selected[1].home.resolve() == Path(home).resolve():
        return selected[1]
    return None


_directory = contextvars.ContextVar("agent_manager_working_directory", default=None)


def current_directory():
    return _directory.get() or Path.cwd().resolve()


@contextlib.contextmanager
def using_directory(directory):
    directory = Path(directory).expanduser().resolve()
    if not directory.is_dir(): raise Error("The saved session working directory is unavailable")
    token = _directory.set(directory)
    try: yield
    finally: _directory.reset(token)
