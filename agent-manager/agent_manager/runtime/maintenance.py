"""Provider maintenance and native profile login for retained application workers."""
from agent_manager.errors import Error
from agent_manager.manager import Manager


def validate_operation(manager, args):
    kind = args["kind"]
    allowed = {"profile-login", "check-profile", "install", "setup-command", "update", "repair", "uninstall", "disable", "enable"}
    if kind not in allowed: raise Error("Unsupported profile-only provider operation")
    if kind in ("profile-login", "check-profile"):
        from agent_manager.profiles.identities import for_environment
        profile = for_environment(manager.state(), args['environment_id'], profile_id=args.get('profile_id'), display=args.get('profile')) if args.get('environment_id') else manager.resolve_profile(args.get('profile'))
        args.update(environment_id=profile.environment_id, profile_id=profile.profile_id, profile=str(profile))
        from agent_manager.environments.operations import check_revision
        check_revision(manager, profile, args.get("expectedRevision"))
        if kind == "profile-login":
            from agent_manager.providers.catalog import authentication
            if args.get("method") is not None and args["method"] not in authentication(manager)["loginMethods"]:
                raise Error("Unsupported sign-in method for this provider")
    elif kind in ("uninstall", "disable", "enable"):
        from agent_manager.providers.installation.software import validate
        validate(manager, kind, args.get("installationRevision"), args.get("confirmed"))
    elif kind in ("install", "update", "repair"):
        plan = args.get('installationPlan')
        if plan is not None and (not isinstance(plan, dict) or plan.get('kind') != kind or plan.get('provider') != manager.provider):
            raise Error('Installation plan does not match the requested provider operation')
        from agent_manager.providers.installation.plans import resolve
        resolve(manager, kind, method=args.get("method"), version_target=args.get("versionTarget"), reinstall=args.get("reinstall", False))


def perform(spec, *, progress=lambda value: None):
    manager = Manager(spec["provider"], config=spec.get("config"), root=spec.get("home"))
    validate_operation(manager, spec)
    kind = spec["kind"]
    if kind in ('profile-login', 'check-profile'):
        from agent_manager.profiles.identities import for_environment
        target = for_environment(manager.state(), spec['environment_id'], profile_id=spec.get('profile_id'), display=spec.get('profile'))
    if kind == "profile-login":
        code = manager.login(target, ["--device-auth"] if spec.get("method") == "device" else [], expected_revision=spec.get("expectedRevision"))
        if code: raise Error(f"Native login exited with status {code}")
        observed = manager.state().get("login_status", {}).get(target.environment_id, {})
        return {"verification": observed.get("verification", "native-command-completed"),
                "state": observed.get("state", "unknown"), "profile": str(target),
                "environment_id": target.environment_id, "profile_id": target.profile_id}
    if kind == "check-profile": return manager.login_status(target)
    if kind == "setup-command":
        from agent_manager.providers.installation.exports import register
        return {"terminalCommand": register(manager, preference=spec.get("terminalPolicy"), progress=progress)}
    if kind in ("uninstall", "disable", "enable"):
        from agent_manager.providers.installation.software import perform as change
        return change(manager, kind, revision=spec.get("installationRevision"), confirmed=spec.get("confirmed"), progress=progress)
    from agent_manager.providers.installation.plans import resolve, validate
    plan = validate(manager, spec["installationPlan"]) if spec.get("installationPlan") else resolve(manager, kind, method=spec.get("method"), version_target=spec.get("versionTarget"), reinstall=spec.get("reinstall", False))
    from agent_manager.providers.catalog import active_profiles
    if active_profiles(manager): raise Error('Stop active profile runs before provider maintenance')
    from agent_manager.providers.installation.plans import execute
    code = execute(manager, plan, progress=progress)
    if code: raise Error(f"Provider maintenance exited with status {code}")
    return {"installationPlan": plan}
