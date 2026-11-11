"""Public profile administration with private native CLI helpers."""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import threading
from agent_manager.storage.platform import check_process
from agent_manager.errors import Error
from agent_manager.storage.files import private_dir
from agent_manager.storage.locks import locks
from agent_manager.storage.registry import read_state, write_state
from agent_manager.providers.configuration import current as launch_configuration


class Manager:
    @property
    def provider_spec(self):
        selected = launch_configuration(self)
        return selected['spec'] if selected is not None else self._provider_spec

    @provider_spec.setter
    def provider_spec(self, value): self._provider_spec = value

    @provider_spec.deleter
    def provider_spec(self): del self._provider_spec

    @property
    def config(self):
        selected = launch_configuration(self)
        return selected['spec'].settings if selected is not None else self._config

    @config.setter
    def config(self, value): self._config = value

    @config.deleter
    def config(self): del self._config

    @property
    def native(self):
        selected = launch_configuration(self)
        return selected['native'] if selected is not None else self._native

    @native.setter
    def native(self, value): self._native = value

    @native.deleter
    def native(self): del self._native

    def __init__(self, provider, config=None, root=None, *, _allow_missing_config=False):
        self._configuration_lock = threading.RLock()
        from agent_manager._paths import state_root, managed_homes_root
        from agent_manager.providers.config import load_registry
        registry = load_registry(config, allow_missing=_allow_missing_config)
        from agent_manager.providers.paths import configuration_path
        self.configuration_source = configuration_path(config).absolute()
        self.provider_spec = registry.resolve(provider)
        self.provider = self.provider_spec.id
        self.config = self.provider_spec.settings
        self.config_path = registry.config_path
        state = state_root(root)
        self.root = state / self.provider
        self.managed_homes = managed_homes_root(state) / self.provider
        self.native = Path(self.config["native_home"]).expanduser().absolute()
        if self.provider_spec.home.opencode and self.config["native_home"] == "~/.local/share/opencode":
            self.native = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "opencode"
        self.lock_root = self.root / ".locks"
        self.state_path = self.root / "state.json"

    def state(self):
        from agent_manager.profiles.identities import empty, validate
        if not self.state_path.exists(): return empty()
        try: return validate(read_state(self.state_path))
        except (ValueError, KeyError, TypeError): raise Error("Invalid profile registry") from None

    def save(self, state):
        from agent_manager.profiles.identities import validate
        from agent_manager.profiles.identities import home_id
        for row in state.get("runs", {}).values():
            if row.get("home"): row["home_id"] = home_id(self, state, row["home"])
        write_state(self.state_path, validate(state))

    def identity_snapshot(self):
        from agent_manager.profiles.identities import snapshot
        return snapshot(self)

    def execution_status(self, run_id):
        """Observe a run without starting, stopping or reconciling execution."""
        import asyncio
        from agent_manager.runtime.observation import execution_status
        return asyncio.run(execution_status(self, run_id))

    def resolve_profile(self, profile=None):
        from agent_manager.profiles.identities import resolve
        return resolve(self.state(), profile)

    def select_profile(self, *, model=None, args=()):
        """Select an execution environment using the normal startup policy."""
        from agent_manager.profiles.selection import select_profile
        return select_profile(self, model=model, args=args)

    def validate_native_config(self, values):
        """Validate/copy explicit native settings without preparing a launch."""
        from agent_manager.runtime.native.configuration import normalize
        return normalize(self, values)

    def home(self, profile):
        from agent_manager.environments.homes import current_home
        return current_home(self, profile)

    def homes(self, profile, *, state=None):
        from agent_manager.environments.homes import profile_homes
        return profile_homes(self, profile, state)

    def profile_directory(self, profile):
        target = self.resolve_profile(profile)
        return self.managed_homes / target.environment_id

    def new_profile(self, profile, model=None):
        from agent_manager.profiles.operations import create_profile
        return create_profile(self, profile, model=model)

    def alias_profile(self, new_name, profile=None, *, environment_id=None):
        from agent_manager.profiles.operations import alias_profile
        return alias_profile(self, new_name, profile, environment_id=environment_id)

    def environments(self):
        from agent_manager.environments.operations import listing
        return listing(self)

    def agent_targets(self):
        """Resource-neutral discovery; no login, native launch or owner imports."""
        from agent_manager.environments.targets import listing
        return listing(self)

    def resolve_agent_target(self, environment_id):
        from agent_manager.environments.targets import describe
        return describe(self, environment_id)

    def lease_native_environment(self, environment_id, *, expected_revision=None):
        """Context manager guarding a local native environment for a process owner."""
        from agent_manager.environments.lease import lease
        return lease(self, environment_id, expected_revision=expected_revision)

    def forget_environment(self, environment_id, *, expected_revision=None):
        from agent_manager.environments.operations import remove
        return remove(self, environment_id, expected_revision=expected_revision)

    def purge_environment(self, environment_id, *, expected_revision=None):
        from agent_manager.environments.operations import remove
        return remove(self, environment_id, purge=True, expected_revision=expected_revision)

    def connect_profile(self, profile, directory):
        from agent_manager.profiles.operations import create_profile
        return create_profile(self, profile, directory=directory)

    def set_default(self, profile, *, expected_revision=None):
        profile = self.resolve_profile(profile)
        with locks(self.lock_root, "registry", wait=True):
            from agent_manager.profiles.identities import resolve
            state = self.state(); profile = resolve(state, profile)
            from agent_manager.profiles.operations import check_alias_revision
            check_alias_revision(self, profile, expected_revision, state)
            state["default_profile_id"] = profile.profile_id
            self.save(state)

    def rename(self, profile, new_name, *, expected_revision=None):
        from agent_manager.profiles.operations import rename_profile
        return rename_profile(self, profile, new_name, expected_revision=expected_revision)

    def login(self, profile=None, login_args=None, *, expected_revision=None):
        from agent_manager.environments.login import login
        return login(self, self.resolve_profile(profile), login_args, expected_revision=expected_revision)

    def login_status(self, profile=None):
        from agent_manager.environments.login import status
        return status(self, self.resolve_profile(profile))

    def prepare_provider_operation(self, arguments):
        """Validate and capture a native maintenance/login intent without executing it.

        Retain the returned descriptor before dispatching it to a worker. Profile
        operations capture immutable environment/alias identities and the current
        environment revision; installations capture an exact approved plan.
        """
        from agent_manager.interfaces.maintenance import prepare
        return prepare(self, arguments)

    def perform_provider_operation(self, operation, *, progress=None):
        """Execute a captured operation in the caller's worker/terminal.

        This synchronous operation may install software or request interactive
        native login. The caller owns durable dispatch, cancellation and receipts.
        """
        from agent_manager.interfaces.maintenance import perform
        return perform(self, operation, progress=progress)

    def models(self, profile=None, *, cwd=None, refresh=False, cached=False, free=False):
        from agent_manager.models import get_models
        return get_models(self, profile, cwd=cwd, refresh=refresh, cached=cached, free=free)

    def integrations(self, profile=None, *, cwd=None, refresh=False, cached=False):
        from agent_manager.integrations.service import get_integrations
        return get_integrations(self, profile, cwd=cwd, refresh=refresh, cached=cached)

    def budget(self, profile=None, *, refresh=False, cached=False, cwd=None, model=None):
        from agent_manager.budgets import get_budget
        return get_budget(self, profile, refresh=refresh, cached=cached, cwd=cwd, model=model)

    def budgets(self, *, refresh=False, cached=False, cwd=None, model=None):
        from agent_manager.budgets import list_budgets
        return list_budgets(self, refresh=refresh, cached=cached, cwd=cwd, model=model)

    def logout(self, profile=None, *, expected_revision=None):
        from agent_manager.environments.login import logout
        return logout(self, self.resolve_profile(profile), expected_revision=expected_revision)

    def working_directory(self):
        from agent_manager.runtime.execution import current_directory
        return current_directory()

    def environment(self, home, *, cwd=None):
        from agent_manager.providers.strategies.homes import environment
        directory = Path(cwd or self.working_directory()).expanduser().resolve()
        variables = environment(self, Path(home))
        if self.provider == "claude" and Path(home).resolve() == (Path.home() / ".claude").resolve():
            variables.pop("CLAUDE_CONFIG_DIR", None)
        return variables | {"PWD": str(directory)}

    def launch_environment(self, home, *, cwd=None, base=None, login=False):
        from agent_manager.runtime.native.profile_environment import environment
        variables = environment(self.provider, dict(os.environ if base is None else base))
        variables.update(self.environment(Path(home), cwd=cwd))
        if self.provider == "claude" and Path(home).resolve() == (Path.home() / ".claude").resolve():
            variables.pop("CLAUDE_CONFIG_DIR", None)
        if self.provider == "claude": variables["HOME"] = str(Path.home())
        return variables

    def launch_command(self, home, arguments, *, provider_cli=True):
        arguments = list(arguments)
        if provider_cli:
            from agent_manager.runtime.execution import current_launch
            from agent_manager.runtime.continuation.coordinator import validate_command
            validate_command(self, current_launch(self, home), arguments)
        if provider_cli and os.name == "nt" and Path(arguments[0]).suffix.lower() in (".cmd", ".ps1"):
            from agent_manager.providers.installation.engine import executable_command
            arguments = executable_command(arguments[0], arguments[1:])
        if provider_cli:
            from agent_manager.runtime.execution import current_launch
            plan = current_launch(self, home)
            if plan and plan.history_view:
                arguments = plan.history_view.command(self, arguments)
        return arguments

    def _invoke_native(self, home, args, *, extra_env=None):
        private_dir(home)
        directory = self.working_directory().resolve()
        variables = self.launch_environment(home, cwd=directory, base=os.environ | (extra_env or {}))
        from agent_manager.runtime.native.launch import terminal_launch
        args, variables = terminal_launch(self, args, variables)
        command = self.launch_command(home, [self.config["executable"], *args])
        return subprocess.call(command, cwd=directory, env=variables)

    @staticmethod
    def active_runs(state, agent):
        runs = state.setdefault("runs", {})
        for key, record in list(runs.items()):
            if record.get("runtime") == "undercurrent":
                # Only authoritative runtime reconciliation releases these leases.
                # A detached launch/cleanup worker is not the managed process.
                continue
            try:
                check_process(record["pid"])
            except ProcessLookupError:
                del runs[key]
            except PermissionError:
                pass
        from agent_manager.profiles.identities import environment_id
        identifier = environment_id(state, agent)
        return [record for record in runs.values() if record.get("environment_id") == identifier]


    def runs_in_home(self, state, agent, home):
        from agent_manager.environments.homes import base_home
        return [record for record in self.active_runs(state, agent)
                if Path(record.get("home", base_home(self, agent))).resolve() == Path(home).resolve()]


    def saved_launch_args(self, agent):
        from agent_manager.providers.saved_args import effective
        return effective(self, agent)

    def refresh_configuration(self):
        from agent_manager.providers.configuration import refresh
        refresh(self)
        return self.configuration(refresh=False)

    def configuration(self, profile=None, *, effective=False, refresh=True):
        from agent_manager.providers.configuration import inspect
        return inspect(self, profile, effective=effective, refresh_config=refresh)

    def launch_arguments(self, profile=None, *, refresh=True):
        if refresh:
            from agent_manager.providers.configuration import refresh as reload
            reload(self)
        from agent_manager.providers.saved_args import configure
        return configure(self, 'list', profile=profile)

    def set_launch_arguments(self, values, profile=None, *, expected_revision=None):
        from agent_manager.providers.saved_args import configure
        return configure(self, 'set', values, profile=profile, expected_revision=expected_revision)

    def native_instructions(self, profile=None):
        from agent_manager.providers.native_instructions import inspect
        return inspect(self, profile)

    def set_native_instructions(self, text, profile=None, *, expected_revision=None):
        from agent_manager.providers.native_instructions import write
        return write(self, text, profile, expected_revision=expected_revision)

    def readiness(self, profile=None, *, refresh=False):
        from agent_manager.providers.readiness import inspect
        return inspect(self, profile, refresh=refresh)

    def launch_args(self, agent, args, model=None):
        if self.provider_spec.home.opencode:
            from agent_manager.providers.opencode import launch_args
            return launch_args(self, agent, args, model)
        if self.provider_spec.models.strategy == "native-argument":
            from agent_manager.providers.arguments import launch_args
            return launch_args(self, agent, args, model)
        selected = ["--model", model] if model is not None else []
        return self.saved_launch_args(agent) + selected + list(args)


    def set_model(self, profile, model, *, expected_revision=None):
        from agent_manager.environments.preferences import set_model
        return set_model(self, profile, model, expected_revision=expected_revision)


    def _run_native(self, profile, args, **options):
        """Internal CLI dispatch; SDK execution is Client.spawn through Undercurrent."""
        from agent_manager.interfaces.cli.execution import run
        return run(self, profile, args, **options)
