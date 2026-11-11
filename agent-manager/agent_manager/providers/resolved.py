"""Immutable provider capabilities; authentication belongs to the native CLI."""
from copy import deepcopy
from dataclasses import dataclass
from agent_manager.errors import Error
from agent_manager.validation import name
from agent_manager.providers.strategies import SESSION_STRATEGIES
from agent_manager.integrations.policy import IntegrationPolicy, resolve as resolve_integrations


@dataclass(frozen=True)
class HomePolicy:
    strategy: str
    environment: str | None
    binding: str
    settings_scope: str
    history_scope: str

    @property
    def shared(self): return self.binding == "shared"

    @property
    def opencode(self): return self.strategy == "opencode-xdg"


@dataclass(frozen=True)
class LoginPolicy:
    args: tuple[str, ...]
    status_args: tuple[str, ...]
    logout_args: tuple[str, ...]


@dataclass(frozen=True)
class SessionPolicy:
    strategy: str | None
    adapter: str | None
    resume_args: tuple[str, ...]
    cross_environment_resume: bool
    cross_environment_resume_reason: str | None = None
    cross_environment_resume_mode: str | None = None
    cross_environment_resume_mechanism: str | None = None

    @property
    def retained_resume(self): return bool(self.resume_args) and self.strategy != "native-latest"


@dataclass(frozen=True)
class ModelDiscoveryPolicy:
    adapter: str = 'disabled'
    versions: tuple[str, ...] = ()
    timeout: int = 30
    cache_ttl: int = 86400

    def capabilities(self):
        return dict(supported=self.adapter != 'disabled', adapter=self.adapter,
            versions=list(self.versions), timeout=self.timeout, cacheTtl=self.cache_ttl,
            verification='fixture-tested' if self.adapter != 'disabled' else 'unsupported')


@dataclass(frozen=True)
class ModelPolicy:
    strategy: str | None
    discovery: ModelDiscoveryPolicy = ModelDiscoveryPolicy()

    @property
    def saved_models(self): return self.strategy in ("opencode-config", "native-argument")


@dataclass(frozen=True)
class BudgetSelectionPolicy:
    strategy: str = 'default'
    buckets: tuple[str, ...] = ()
    model_families: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class BudgetPolicy:
    adapter: str = 'disabled'
    versions: tuple[str, ...] = ()
    timeout: int = 20
    cache_ttl: int = 300
    reason: str | None = None
    selection: BudgetSelectionPolicy = BudgetSelectionPolicy()

    def capabilities(self):
        return dict(supported=self.adapter != 'disabled', adapter=self.adapter,
            scope='provider-account', versions=list(self.versions), cacheTtl=self.cache_ttl,
            verification='fixture-tested' if self.adapter != 'disabled' else 'unsupported',
            automaticSelection=self.selection.strategy,
            reason=self.reason)


@dataclass(frozen=True)
class InstructionPolicy:
    strategy: str = 'disabled'
    filename: str | None = None
    overrides: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()

    def capabilities(self):
        return dict(supported=self.strategy != 'disabled', strategy=self.strategy,
            filename=self.filename, overrides=list(self.overrides), paths=list(self.paths))


@dataclass(frozen=True)
class ResolvedProvider:
    id: str
    source: str
    home: HomePolicy
    login: LoginPolicy
    sessions: SessionPolicy
    models: ModelPolicy
    settings: dict
    budget: BudgetPolicy = BudgetPolicy()
    integrations: IntegrationPolicy = IntegrationPolicy()
    service: str | None = None
    instructions: InstructionPolicy = InstructionPolicy()

    def capabilities(self, *, version=None, version_checked=False):
        from agent_manager.providers.terminal_policy import continuation
        feature = continuation(self.id, version, checked=version_checked,
                               mechanism=self.sessions.cross_environment_resume_mechanism)
        enabled = self.sessions.cross_environment_resume
        reason = self.sessions.cross_environment_resume_reason
        if enabled and feature and version_checked and not feature['compatible']:
            enabled, reason = False, feature['reason']
        budget = self.budget.capabilities()
        if self.integrations.adapter != 'disabled':
            budget.update(supported=True, adapter=self.integrations.adapter, scope='provider-connection',
                versions=list(self.integrations.versions), cacheTtl=self.integrations.cache_ttl,
                verification='fixture-tested', reason='Reviewed connection budgets only; missing account allowances remain unknown.')
        return dict(service=self.service, createProfile=True, connectProfile=True, profileAliases=True,
            multipleEnvironments=not self.home.shared, maxEnvironments=1 if self.home.shared else None,
            duplicateProfile=not self.home.shared,
            isolatedHomes=not self.home.shared, homeBinding=self.home.binding,
            settingsScope=self.home.settings_scope, historyScope=self.home.history_scope,
            homeStrategy=self.home.strategy, nativeLogin=True,
            loginStatus=bool(self.login.status_args), logout=bool(self.login.logout_args),
            retainedSessionResume=self.sessions.retained_resume,
            nativeResumeMayCreateSession=self.sessions.strategy == "native-latest",
            crossEnvironmentResume=enabled,
            crossEnvironmentResumeReason=reason,
            crossEnvironmentResumeMode=self.sessions.cross_environment_resume_mode,
            crossEnvironmentResumeCompatibility=feature,
            savedModels=self.models.saved_models, modelDiscovery=self.models.discovery.capabilities(),
            integrations=self.integrations.capabilities(), budget=budget,
            nativeInstructions=self.instructions.capabilities())


def resolve_provider(provider, definition, preferences, *, services=None, definitions=None):
    if definition is None:
        raise Error("Provider requires a version-three manifest")
    settings = deepcopy(preferences)
    if settings.setdefault("runtime", "direct") not in ("direct", "undercurrent"):
        raise Error("runtime must be direct or undercurrent")
    for key in ("executable", "native_home"):
        if not isinstance(settings.get(key), str) or not settings[key] or "\0" in settings[key]:
            raise Error(f"Provider configuration requires {key}")
    name(settings.setdefault("detection_profile", provider))
    for key in ("run_args", "launch_args", "app_server_args"):
        value = settings.setdefault(key, [])
        if not isinstance(value, list) or any(not isinstance(v, str) or "\0" in v for v in value):
            raise Error(f"{key} must be an array of literal arguments")
    terminal = definition.terminal
    home = terminal["home"]
    if home["strategy"] == "native-shared" and settings["native_home"] != home["default_path"]:
        raise Error("This shared-home provider does not support redirecting its native home")
    login = terminal["login"]
    sessions = terminal.get("sessions", {})
    budget = dict(terminal.get('budget', {}))
    budget['versions'] = tuple(budget.get('versions', ()))
    selection = budget.get('selection', {})
    budget['selection'] = BudgetSelectionPolicy(selection.get('strategy', 'default'),
        tuple(selection.get('buckets', ())), tuple((key, tuple(value))
        for key, value in selection.get('model_families', {}).items()))
    from agent_manager.runtime.continuation.registry import availability
    mechanism = sessions.get('cross_environment_resume')
    cross_environment_resume, reason, mode = availability(provider, mechanism, sessions.get('cross_environment_resume_reason'))
    return ResolvedProvider(provider, definition.source,
        HomePolicy(home["strategy"], home.get("environment"), home["binding"], home["settings_scope"], home["history_scope"]),
        LoginPolicy(tuple(login["args"]), tuple(login.get("status_args", ())), tuple(login.get("logout_args", ()))),
        SessionPolicy(sessions.get("strategy"), SESSION_STRATEGIES.get(sessions.get("strategy")),
            tuple(sessions.get("resume_args", ())), cross_environment_resume, reason,
            mode,
            mechanism),
        ModelPolicy(terminal.get("models", {}).get("strategy"), ModelDiscoveryPolicy(**{
            **terminal.get('models', {}).get('discovery', {}),
            'versions': tuple(terminal.get('models', {}).get('discovery', {}).get('versions', ())) })), settings,
        BudgetPolicy(**budget), resolve_integrations(terminal.get('integrations', {}),
            services=services, definitions=definitions), definition.service,
        InstructionPolicy(**{**terminal.get('instructions', {}),
            'overrides': tuple(terminal.get('instructions', {}).get('overrides', ())),
            'paths': tuple(terminal.get('instructions', {}).get('paths', ()))}))
