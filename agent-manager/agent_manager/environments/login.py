"""Provider-owned login, status and logout in the selected profile home."""
import json
import subprocess
import time
from agent_manager.errors import Error
from agent_manager.storage.locks import locks, shared_environment_keys
from agent_manager.environments.operations import check_revision


def _record(manager, profile, observation):
    with locks(manager.lock_root, "registry", wait=True):
        state = manager.state()
        state.setdefault("login_status", {})[profile.environment_id] = observation
        manager.save(state)
    return observation


def _idle(manager, profile):
    from agent_manager.runtime.lifecycle import reconcile
    reconcile(manager, profile)
    state = manager.state()
    if manager.active_runs(state, profile): raise Error("Stop this profile's managed runs before changing its native login")


def login(manager, profile, args=None, *, expected_revision=None):
    profile = manager.resolve_profile(profile)
    identifier = profile.environment_id
    with locks(manager.lock_root, "environment-" + identifier, wait=True):
        check_revision(manager, profile, expected_revision); _idle(manager, profile)
        from agent_manager.budgets.service import invalidate
        invalidate(manager, identifier)
        from agent_manager.models.service import invalidate as invalidate_models
        invalidate_models(manager, identifier)
        from agent_manager.integrations.service import invalidate as invalidate_integrations
        invalidate_integrations(manager, identifier)
        code = manager._invoke_native(manager.home(profile), [*manager.provider_spec.login.args, *(args or ())])
        if code == 0:
            _record(manager, profile, dict(state="unknown", verification="native-command-completed", checked_at=time.time()))
            if manager.provider_spec.login.status_args:
                _status(manager, profile)
        return code


def status(manager, profile):
    profile = manager.resolve_profile(profile)
    # A status read must coexist with Codex's long-lived native sessions.
    # Fail promptly during exclusive maintenance rather than waiting beyond
    # the caller's request deadline before even starting the bounded probe.
    with locks(manager.lock_root, "environment-" + profile.environment_id,
               shared_keys=shared_environment_keys(manager, profile.environment_id)):
        return _status(manager, profile)


def _status(manager, profile):
    args = manager.provider_spec.login.status_args
    if not args: return dict(state="unknown", verification="unsupported")
    home = manager.home(profile)
    try:
        result = subprocess.run(manager.launch_command(home, [manager.config["executable"], *args]),
            env=manager.launch_environment(home), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return _record(manager, profile, dict(state="unknown", verification="unavailable", checked_at=time.time()))
    # Never retain or relay provider output: it can contain account data or tokens.
    state = "unknown"
    if manager.provider == "claude":
        try:
            value = json.loads(result.stdout)
            if value.get("loggedIn") is False: state = "signed-out"
            elif value.get("loggedIn") is True and result.returncode == 0: state = "signed-in"
        except (ValueError, AttributeError): pass
    elif manager.provider == "codex" and result.returncode == 0:
        state = "signed-in"
    elif manager.provider == 'codex' and 'not logged in' in (result.stdout + result.stderr).casefold():
        state = 'signed-out'
    return _record(manager, profile, dict(state=state, verification="native-status", checked_at=time.time()))


def logout(manager, profile, *, expected_revision=None):
    args = manager.provider_spec.login.logout_args
    if not args: raise Error("Use the provider's interactive logout in this profile's native terminal")
    profile = manager.resolve_profile(profile)
    identifier = profile.environment_id
    with locks(manager.lock_root, "environment-" + identifier, wait=True):
        check_revision(manager, profile, expected_revision); _idle(manager, profile)
        from agent_manager.budgets.service import invalidate
        invalidate(manager, identifier)
        from agent_manager.models.service import invalidate as invalidate_models
        invalidate_models(manager, identifier)
        from agent_manager.integrations.service import invalidate as invalidate_integrations
        invalidate_integrations(manager, identifier)
        code = manager._invoke_native(manager.home(profile), list(args))
        if code == 0: _record(manager, profile, dict(state="signed-out", verification="native-command-completed", checked_at=time.time()))
        return code
