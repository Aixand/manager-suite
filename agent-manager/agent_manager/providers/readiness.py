"""Bounded, structured setup diagnostics shared by CLI and headless clients."""
import shutil
from pathlib import Path

from agent_manager.errors import Error


def setup_state(manager):
    from agent_manager.providers.installation.software import disabled
    profiles = manager.state()['profiles']
    path = shutil.which(manager.config['executable'])
    enabled = not disabled(manager)
    return dict(enabled=enabled, executable=path, profile_count=len(profiles),
        setup_required=not enabled or path is None or not profiles)


def login_observation(manager, profile, *, refresh=False):
    target = manager.resolve_profile(profile)
    return manager.login_status(target) if refresh else manager.state().get('login_status', {}).get(
        target.environment_id, dict(state='unknown', verification='unchecked'))


def feature_versions(manager, installed):
    from agent_manager.providers.terminal_policy import recommended
    try: target = recommended(manager.provider)
    except (KeyError, ValueError): target = None
    policies = dict(models=manager.provider_spec.models.discovery,
        budget=manager.provider_spec.integrations if manager.provider_spec.integrations.adapter != 'disabled' else manager.provider_spec.budget)
    return {key: dict(adapter=policy.adapter, supported=policy.adapter != 'disabled',
        installed_version=installed, installed_supported=installed in policy.versions if installed else None,
        recommended_version=target, recommended_supported=target in policy.versions if target else None,
        qualified_versions=list(policy.versions), verification='fixture-tested' if policy.adapter != 'disabled' else 'unsupported')
        for key, policy in policies.items()}


def inspect(manager, profile=None, *, refresh=False):
    from agent_manager.providers.installation import engine
    setup = setup_state(manager)
    spec = engine.catalog().get(manager.provider)
    found = (engine.inspect(spec, Path(setup['executable'])) if spec else engine.inspect_external(manager)) if setup['executable'] else None
    state = manager.state()
    target = manager.resolve_profile(profile) if profile is not None else None
    if target is None and state.get('default_profile_id'): target = manager.resolve_profile()
    if target is None and len(state['profiles']) == 1: target = manager.resolve_profile(next(iter(state['profiles'])))
    issues = []
    def issue(code, message, command, severity='warning'):
        issues.append(dict(code=code, message=message, command=command, severity=severity))
    if not setup['enabled']: issue('disabled', 'Provider is disabled.', f'agent {manager.provider} onboarding', 'error')
    if not setup['executable']: issue('missing-cli', 'Provider executable is unavailable.', f'agent install {manager.provider}', 'error')
    if not setup['profile_count']: issue('missing-profile', 'No profile is configured.', f'agent {manager.provider} onboarding', 'error')
    if target is None and setup['profile_count']:
        issue('choose-profile', 'Choose a profile for authentication and allowance diagnostics.', f'agent {manager.provider} doctor --profile PROFILE')
    version = found.version if found else None
    if found and found.error:
        issue('installation-check', found.error, f'agent versions {manager.provider}')
    compatibility = None
    if version and spec:
        from agent_manager.providers.terminal_policy import compatibility as check
        compatibility = check(manager.provider, version)
        if not compatibility['compatible']:
            issue('incompatible-cli', compatibility['reason'], f'agent update {manager.provider}', 'error')
    features = feature_versions(manager, version)
    for name, feature in features.items():
        if feature['supported'] and version and not feature['installed_supported']:
            issue('unsupported-' + name, f'{name}: installed {version}; qualified versions: ' + ', '.join(feature['qualified_versions']),
                f'agent {manager.provider} doctor --json')
        if feature['supported'] and feature['recommended_version'] and not feature['recommended_supported']:
            issue('qualification-pending-' + name, f'{name} is not qualified for recommended CLI {feature["recommended_version"]}.',
                f'agent versions {manager.provider}')
    login = login_observation(manager, target, refresh=refresh and bool(setup['executable'])) if target is not None else None
    budget = None
    if target is not None:
        if login['state'] == 'signed-out':
            issue('authentication', 'The native CLI reports signed out.', f'agent {manager.provider} login {target}', 'error')
        elif login['state'] == 'unknown':
            if manager.provider_spec.login.status_args:
                issue('login-unverified', 'Native sign-in has not been verified.', f'agent {manager.provider} status {target}')
            else:
                issue('login-status-unsupported', 'This provider has no qualified native sign-in status probe.', f'agent {manager.provider} run {target}', 'info')
        try:
            budget = manager.budget(target, refresh=True) if refresh and setup['executable'] else manager.budget(target, cached=True)
        except (Error, OSError) as error:
            budget = dict(status='unavailable', error=dict(code='unavailable', message=str(error)))
        if not refresh and budget.get('observed_at') is None and not budget.get('error'):
            issue('allowance-unchecked', 'No cached allowance observation is available.', f'agent {manager.provider} budget {target} --refresh', 'info')
        elif budget['status'] not in ('ok', 'partial'):
            issue('allowance-' + budget['status'], (budget.get('error') or {}).get('message') or 'A fresh allowance observation is unavailable.',
                f'agent {manager.provider} budget {target} --refresh')
    return dict(schema='agent-manager/readiness-1', provider=manager.provider,
        profile=str(target) if target is not None else None,
        environment_id=target.environment_id if target is not None else None,
        ready=not any(row['severity'] == 'error' for row in issues),
        setup=setup, installation=dict(path=setup['executable'], version=version, error=found.error if found else None, compatibility=compatibility),
        features=features, login=login, budget=budget, issues=issues)
