"""Durable references to provider-owned sessions, independent of profile names."""
import hashlib
from pathlib import Path
import time
from agent_manager.errors import Error
from agent_manager.storage.locks import locks

_UNSET = object()


def reference(provider, home, native_id):
    return hashlib.sha256((provider + "\0" + str(Path(home).resolve()) + "\0" + native_id).encode()).hexdigest()


def remember(manager, profile, native_id, *, cwd, history_home=None, title=None, updated=None,
             rollout_path=None, history_mode=None, archived=None, parent_id=_UNSET):
    with locks(manager.lock_root, "registry", wait=True):
        state = manager.state()
        profile = manager.resolve_profile(profile)
        row = state["environments"][profile.environment_id]
        route = state.get('session_routes', {}).get(profile.environment_id, {}).get(native_id)
        if route is not None and history_home is None:
            history_home = state['sessions'][route]['history_home']
        home = str(Path(history_home or row["home"]).resolve())
        key = reference(manager.provider, home, native_id)
        prior = state["sessions"].get(key, {})
        record = dict(prior, ref=key, provider=manager.provider, native_id=native_id,
            origin_environment_id=prior.get("origin_environment_id", profile.environment_id), history_home=home,
            cwd=str(Path(cwd).resolve()), title=title or prior.get("title", native_id),
            updated=updated or prior.get("updated", time.time()))
        if rollout_path is not None: record["rollout_path"] = str(Path(rollout_path).resolve())
        if history_mode is not None: record["history_mode"] = history_mode
        if archived is not None: record["archived"] = archived
        if parent_id is not _UNSET: record["parent_id"] = parent_id
        state["sessions"][key] = record; manager.save(state)
    return record


def listing(manager, *, cwd=None, refresh=True, recursive=False, include_archived=False, include_children=False):
    if type(include_archived) is not bool or type(include_children) is not bool:
        raise ValueError("Session history flags must be boolean")
    if not isinstance(recursive, bool): raise ValueError("recursive must be a boolean")
    if recursive and cwd is None: raise ValueError("recursive discovery requires cwd")
    directory = Path(cwd).expanduser().resolve() if cwd is not None else None
    if refresh:
        from agent_manager.runtime.continuation.registry import for_manager
        for profile, item in for_manager(manager).discover(manager, None if recursive else directory,
                include_archived=include_archived, include_children=include_children):
            remember(manager, profile, **item)
    rows = [row for row in manager.state()["sessions"].values()
            if (include_archived or not row.get("archived")) and (include_children or not row.get("parent_id"))]
    if directory is not None:
        rows = [row for row in rows if (Path(row["cwd"]).resolve().is_relative_to(directory) if recursive
                else Path(row["cwd"]).resolve() == directory)]
    return sorted(rows, key=lambda row: (-row["updated"], row["ref"]))


def find(manager, value, *, refresh=True):
    available = listing(manager, refresh=refresh)
    from agent_manager.runtime.continuation.registry import for_manager
    logical = for_manager(manager).logical_source(manager, value, available)
    if logical is not None: return logical
    rows = [row for row in available if value in (row["ref"], row["native_id"])]
    if not rows: raise Error("Session was not found; discover it or use the provider's native session picker")
    if len(rows) != 1: raise Error("Session ID exists in several native homes; select its full session reference")
    return rows[0]


def check_reference(manager, saved, *, state=None):
    state = manager.state() if state is None else state
    current = state["sessions"].get(saved["ref"])
    keys = ("provider", "native_id", "history_home", "origin_environment_id", "cwd", "rollout_path", "history_mode")
    if (current is None or saved["provider"] != manager.provider
            or saved["ref"] != reference(manager.provider, saved["history_home"], saved["native_id"])
            or any(current.get(key) != saved.get(key) for key in keys)):
        raise Error("The source session reference changed; select the session again")


def select(manager, value, profile=None):
    """Read retained metadata only; discovery and preparation are explicit stages."""
    require_retained_resume(manager)
    from agent_manager.profiles.identities import for_environment
    saved = find(manager, value, refresh=False)
    profile = manager.resolve_profile(profile) if profile is not None else for_environment(manager.state(), saved["origin_environment_id"])
    from agent_manager.runtime.continuation.coordinator import capture
    return capture(manager, saved, profile, logical=value == saved.get('conversation_id'))


def require_retained_resume(manager):
    policy = manager.provider_spec.sessions
    if not policy.resume_args: raise Error("This provider does not declare native continuation")
    if not policy.retained_resume:
        raise Error("Exact retained-session continuation is unavailable for this provider. "
            "Its native latest-session shortcut can start a new conversation; "
            "open the profile terminal and use its native history picker.")


def execution_profiles(manager, saved):
    """Only offer homes that can actually continue the selected session."""
    if not manager.provider_spec.sessions.retained_resume: return []
    state = manager.state()
    profiles = state["profiles"]
    home = Path(saved["history_home"]).resolve()
    return sorted(profile for profile, row in profiles.items()
        if manager.provider_spec.sessions.cross_environment_resume
        or Path(state["environments"][row["environment_id"]]["home"]).resolve() == home)


def validate_execution_home(manager, saved, home):
    if home is not None and Path(home).resolve() == Path(saved["history_home"]).resolve():
        return
    if not manager.provider_spec.sessions.cross_environment_resume:
        reason = manager.provider_spec.sessions.cross_environment_resume_reason or "No native continuation adapter is qualified for this provider."
        raise Error("Cross-environment native continuation is unavailable. " + reason
            + " Select or connect the original history home: " + saved["history_home"])


def validate_source(manager, saved, profile):
    require_retained_resume(manager)
    if saved["provider"] != manager.provider: raise Error("Cross-provider continuation is unsupported")
    home = Path(saved["history_home"])
    if not home.is_dir(): raise Error("The original native session history home is unavailable")
    from agent_manager.runtime.continuation.registry import for_manager
    for_manager(manager).validate_source(manager, saved, profile)


def continue_session(manager, value, *, profile=None, args=(), model=None, **options):
    require_retained_resume(manager)
    if args: raise Error("Continue uses captured session settings; forwarded overrides are not supported")
    from agent_manager.runtime.continuation.coordinator import prepare, resolve_request
    plan = prepare(manager, resolve_request(manager, value, profile))
    saved, profile = dict(plan.session), plan.target
    return manager._run_native(profile, [*manager.provider_spec.sessions.resume_args, plan.native_id],
        conversation_id=plan.native_id, model=model, cwd=saved["cwd"],
        _source_session=saved, _continuation_plan=plan, **options)


def for_resume(manager, profile, native_id):
    """Resolve a native ID in one environment without inventing a second origin."""
    profile = manager.resolve_profile(profile); state = manager.state()
    route = state.get('session_routes', {}).get(profile.environment_id, {}).get(native_id)
    if route is not None:
        saved = state['sessions'].get(route)
        if saved is None or saved['native_id'] != native_id: raise Error('The native continuation route is unavailable')
        return dict(saved)
    home = manager.home(profile).resolve()
    matches = [row for row in state['sessions'].values()
               if row['native_id'] == native_id and Path(row['history_home']).resolve() == home]
    if len(matches) > 1: raise Error('Choose the full native session reference')
    if matches: return dict(matches[0])
    matches = [row for row in listing(manager) if row['native_id'] == native_id and Path(row['history_home']).resolve() == home]
    return dict(matches[0]) if matches else None
