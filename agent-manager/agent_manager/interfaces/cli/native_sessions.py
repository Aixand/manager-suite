"""Delegate undiscovered session selection to the provider's native picker."""
def native_session(manager, args, *, picker, profile=None, model=None, **options):
    from agent_manager.errors import Error
    from agent_manager.profiles.sessions import require_retained_resume
    require_retained_resume(manager)
    if profile is None:
        from agent_manager.interfaces.cli.profile_picker import pick_profile, verify
        profiles = sorted(manager.state()["profiles"])
        if not profiles:
            from agent_manager.errors import Error
            raise Error("Create or connect a profile first")
        choice = pick_profile(manager, profiles, picker=picker, title='Choose a profile',
            detail='Then choose a native session', model=model, args=args)
        if choice is None: return 0
        profile = choice.target
        options['validate'] = lambda: verify(manager, choice)
    return manager._run_native(profile, [*manager.provider_spec.sessions.resume_args, *args], model=model, **options)
