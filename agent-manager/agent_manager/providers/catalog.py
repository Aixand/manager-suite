"""Versioned local provider inventory for CLI, SDK and application clients.

Discovery does not start agent sessions, check remote quotas, or expose credentials.
Provider failures are reported individually; configuration failures are not empty
inventories. Installation and account readiness are separate observations.
"""
from agent_manager.profiles.identities import environment_id, resolve
from concurrent.futures import ThreadPoolExecutor

from agent_manager.providers.installation import engine as install, updates as update
from agent_manager.errors import Error
from agent_manager.manager import Manager
from agent_manager.providers.config import load

SCHEMA = "agent-manager/provider-catalog-1"


def active_profiles(manager):
    state = manager.state()
    from agent_manager.profiles.identities import for_environment
    targets = [for_environment(state, identifier) for identifier in state['environments']]
    return [str(target) for target in targets if manager.active_runs(state, target)]


def inspect_installation(manager):
    from agent_manager.providers.installation.software import plan
    provider = manager.provider
    spec = install.catalog().get(provider)
    candidate = update.candidate(provider, spec, manager.config_path, manager.root.parent) if spec else None
    found = candidate.installation if candidate else install.inspect_external(manager)
    software = plan(manager, found) if found else plan(manager)
    result = dict(provider=provider, installed=found is not None,
        software=software,
        usable=found is not None and not found.error and not software['disabled'], version=found.version if found else None,
        method=found.method if found else None, path=str(found.path) if found else None,
        canUpdate=bool(candidate and found and not candidate.error),
        error=found.error if software['bundled'] and found else candidate.error if candidate else found.error if found else 'CLI executable not found.',
        activeProfiles=active_profiles(manager),
        methods=[dict(id=method, label=install.METHODS[method], error=install.prerequisites(spec, method))
                 for method in install.supported_methods(spec)] if spec else [],
        setup=install.setup_guide(provider),
        **({'maintenanceNote': install.BUNDLED_NOTE} if software['bundled'] else {}))
    result['terminalUsable'] = result['usable']
    from agent_manager.providers.terminal_policy import compatibility, recommended
    if spec:
        result['compatibility'] = compatibility(provider, found.version if found else None)
        result['recommendedVersion'] = recommended(provider)
        from agent_manager.providers.installation.releases import catalog as release_catalog
        result['releases'] = release_catalog(provider)
        result['latestVersion'] = result['releases']['latestVersion']
    from agent_manager.providers.installation.exports import status as terminal_commands
    result['terminalCommand'] = terminal_commands(manager)
    return result


def authentication(manager, *, installation=None):
    versions = dict(version=installation.get('version'), version_checked=True) if installation is not None else {}
    return dict(manager.provider_spec.capabilities(**versions), terminal=dict(launch=True, authentication="native-cli"),
        loginMethods=["browser", "device"] if manager.provider == "codex" else ["terminal"],
        defaultLoginMethod="browser" if manager.provider == "codex" else "terminal")


def profile_readiness(manager, profile, *, installation=None, model=None):
    from agent_manager.providers.readiness import setup_state, login_observation, feature_versions
    state = manager.state()
    try: profile = resolve(state, profile)
    except Error: return dict(ready=False, reason="Profile no longer exists")
    info = installation if installation is not None else inspect_installation(manager)
    enabled = setup_state(manager)['enabled']
    login = login_observation(manager, profile)
    terminal = dict(ready=enabled and info.get("terminalUsable", False), verification=login.get("verification", "unchecked"),
        state=login.get("state", "unknown"), reason=None if enabled and info.get("terminalUsable") else "Check the native provider installation")
    return dict(ready=terminal["ready"], reason=terminal.get("reason"), verification=terminal.get("verification", "unchecked"),
        terminal=terminal, model=model or state.get("models", {}).get(environment_id(state, profile)),
        featureCompatibility=feature_versions(manager, info.get('version')))


def inspect_provider(manager):
    from agent_manager.profiles.listing import list_profiles
    info = inspect_installation(manager); caps = authentication(manager, installation=info)
    profiles = list_profiles(manager)
    for profile in profiles:
        profile['capabilities'] = manager.provider_spec.capabilities(version=info.get('version'), version_checked=True)
        profile["readiness"] = profile_readiness(manager, profile["profile"], installation=info)
    return dict(info, label=manager.config.get("label", manager.provider),
        description=manager.config.get("description", "Local agent provider"), configured=bool(profiles),
        available=True, capabilities=caps, profiles=profiles,
        capability=dict(provider=manager.provider, label=manager.config.get("label", manager.provider), installed=info["installed"], **caps))


def catalog(*, config=None, home=None):
    definitions, _ = load(config)

    def inspect(provider):
        try:
            return inspect_provider(Manager(provider, config=config, root=home))
        except (Error, OSError, ValueError, RuntimeError) as error:
            return dict(provider=provider, label=definitions[provider].get('label', provider),
                description=definitions[provider].get('description', 'Local agent provider'),
                available=False, installed=False, usable=False, version=None, path=None,
                method=None, canUpdate=False, methods=[], activeProfiles=[],
                error=str(error), configured=None, profiles=[])

    with ThreadPoolExecutor(max_workers=4) as pool:
        providers = list(pool.map(inspect, definitions))
    return dict(schema=SCHEMA, environment=install.environment_info(), providers=providers,
        configured=any(row.get('configured') for row in providers),
        inventoryComplete=all(row.get('configured') is not None for row in providers))
