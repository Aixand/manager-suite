"""Automatic startup delegates account selection and normal native launch guards."""
import sys


def start(manager, args, **options):
    from agent_manager.providers.installation.software import require_enabled
    from agent_manager.profiles.selection import choose, verify, SelectionUnavailable
    from agent_manager.interfaces.cli import onboarding
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    from agent_manager.providers.readiness import setup_state
    if setup_state(manager)['setup_required']:
        if not interactive:
            from agent_manager.errors import Error
            raise Error(f'Setup is incomplete. Run agent {manager.provider} onboarding in an interactive terminal.')
        target = onboarding.setup(manager)
        if target is None or not onboarding.authenticate(manager, target): return 0
        return manager._run_native(target, args, **options)
    require_enabled(manager)
    try:
        decision = choose(manager, args=args, model=options.get('model'))
    except SelectionUnavailable as error:
        if not interactive: raise
        print(str(error), file=sys.stderr)
        target = onboarding.select(manager)
        if target is None or not onboarding.authenticate(manager, target): return 0
        return manager._run_native(target, args, **options)
    verify(manager, decision)
    detail = ('unlimited allowance' if decision.score.unlimited else
              f'{decision.score.remaining_percent:g}% remaining in its limiting quota window') if decision.score else decision.reason
    print(f'agent: selected {manager.provider} profile {decision.target} ({detail}).', file=sys.stderr)
    if decision.budget is not None and decision.budget['scope'] == 'provider-connection':
        from agent_manager.providers.arguments import effective_model
        if effective_model(manager, decision.target, args, options.get('model')) is None:
            # Launch the model whose paying route was inspected, even if the
            # native CLI's most recently used model changes after selection.
            options['model'] = decision.model
    return manager._run_native(decision.target, args, validate=lambda: verify(manager, decision), **options)
