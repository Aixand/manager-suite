"""Choose a native conversation followed by its execution profile."""
from pathlib import Path
from agent_manager.errors import Error


def continue_session(manager, args=(), *, session=None, profile=None, picker=None, model=None, **options):
    from agent_manager.profiles.sessions import check_reference, find, require_retained_resume, execution_profiles, listing, validate_execution_home, continue_session as launch
    require_retained_resume(manager)
    from agent_manager.providers.installation.software import require_enabled
    require_enabled(manager)
    if session is not None and profile is not None:
        return launch(manager, session, profile=profile, args=args, model=model, **options)
    if picker is None:
        from agent_manager.interfaces.cli.tui import pick, require_terminal
        require_terminal()
        picker = pick
    if session is None:
        rows = listing(manager, cwd=Path.cwd())
        if not rows:
            from agent_manager.interfaces.cli.native_sessions import native_session
            return native_session(manager, args, picker=picker, profile=profile, model=model, **options)
        from agent_manager.runtime.continuation.registry import for_manager
        columns, choices = for_manager(manager).picker(manager, rows)
        selected = picker("Choose a native session", columns, choices, manager.provider)
        if selected is None: return 0
        saved = rows[selected]
        session = saved['ref']
    else:
        saved = find(manager, session)
    if args: raise Error('Continue uses captured session settings; forwarded overrides are not supported')
    if profile is None:
        from agent_manager.interfaces.cli.profile_picker import pick_profile, verify
        profiles = execution_profiles(manager, saved)
        if not profiles:
            validate_execution_home(manager, saved, None)
            raise Error("Connect an execution profile before continuing a session")
        choice = pick_profile(manager, profiles, picker=picker, title='Choose an execution profile',
            detail='Use the selected native login', model=model)
        if choice is None: return 0
        profile = choice.target
        options['validate'] = lambda: verify(manager, choice)
    check_reference(manager, saved)
    return launch(manager, session, profile=profile, args=args, model=model, **options)
