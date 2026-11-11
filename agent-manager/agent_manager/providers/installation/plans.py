"""One exact, reviewable installation plan for CLI and retained maintenance jobs."""
from pathlib import Path

from agent_manager.errors import Error
from agent_manager.providers import terminal_policy
from agent_manager.providers.installation import engine as install, updates as update, releases
from agent_manager.providers.policy import compatibility, revision, version_tuple


def resolve(manager, kind, *, method=None, version_target=None, reinstall=False, allow_unavailable=False, candidate=None):
    if kind not in ('install', 'update', 'repair'): raise Error('This operation has no CLI installation plan')
    if method is not None and method != 'installer':
        raise Error('Only official native installers are supported.')
    selected_version = releases.selector(version_target)
    spec = install.catalog()[manager.provider]
    candidate = candidate or update.candidate(manager.provider, spec, manager.config_path, manager.root.parent)
    current = candidate.installation
    if kind == 'update' and current is None:
        raise Error(candidate.error or 'CLI executable not found; run agent install first')
    if current and current.method == 'bundled' and kind in ('update', 'repair'):
        raise Error(install.BUNDLED_NOTE)
    if current and current.method not in ('installer', 'bundled') and kind in ('update', 'repair'):
        raise Error('This CLI was installed externally. Agent Manager only manages native installations; migrate it manually first.')
    compatible = current is not None and not current.error and compatibility(manager.provider, current.version)['compatible']
    if current is None and manager.config['executable'] != spec['executable']:
        owned = False
        for choice in [method] if method else install.supported_methods(spec):
            try: owned = Path(manager.config['executable']).absolute() == install.destination(spec, choice).absolute()
            except Error: continue
            if owned: break
        if not owned: raise Error(candidate.error or 'Resolve the configured executable override before installation.')
    if current and method and method != current.method:
        raise Error('The installed provider uses another method; migrate it manually before changing methods.')

    # Defaults preserve compatible software. Explicit selectors describe a requested
    # release; repair without a selector preserves the installed compatible version.
    target = terminal_policy.recommended(manager.provider)
    effective_method = method or (current.method if current else None)
    if selected_version is not None:
        target = releases.resolve(manager.provider, selected_version,
                                  method=effective_method if selected_version == 'latest' else None)
    if current and kind == 'repair' and selected_version is None:
        if not compatible:
            raise Error('The installed release is incompatible. Choose Restore recommended version to change it explicitly.')
        target = current.version
    keep = bool(compatible and (
        kind == 'install' and not reinstall and (selected_version is None or current.version == target)
        or kind == 'update' and (current.version == target or selected_version is None and version_tuple(current.version) >= version_tuple(target))))
    if current and current.method == 'bundled' and not keep:
        raise Error(install.BUNDLED_NOTE)
    if current and current.method not in ('installer', 'bundled') and not keep:
        raise Error('This CLI was installed externally. Agent Manager only manages native installations; migrate it manually first.')
    if current and kind == 'install' and not reinstall and not keep:
        raise Error((current.error + '; ' if current.error else '') + 'A different version is installed; use --reinstall to change it. Incompatible releases require Restore recommended version.')
    older = bool(current and version_tuple(current.version) and version_tuple(current.version) < version_tuple(target))
    if current and current.error and kind == 'update': raise Error(candidate.error or current.error)
    if current and kind == 'update' and not compatible and selected_version is None and not older:
        raise Error('Choose Restore recommended version or an explicit --version to replace an incompatible installation.')

    selected = effective_method
    if not selected and not keep:
        selected = next((choice for choice in install.supported_methods(spec) if not install.prerequisites(spec, choice, target)),
                        next(iter(install.supported_methods(spec)), None))
    blocked = None
    if not keep:
        # Repair may preserve a currently compatible release outside the approved
        # catalog. A fresh release selection always comes from that catalog.
        if not (kind == 'repair' and selected_version is None):
            target = releases.resolve(manager.provider, target)
        if candidate.error and current:
            method_errors = {install.prerequisites(spec, current.method, target), install.prerequisites(spec, current.method)}
            repair_probe = bool((kind == 'repair' or reinstall) and current.error
                                and candidate.error == current.error and current.method == 'installer')
            if repair_probe:
                problem = install.migration_error(install.discover(spec), current.method)
                if problem: raise Error(problem)
                if current.path.absolute() != install.destination(spec, current.method).absolute():
                    raise Error('The configured executable is outside the managed installation location.')
            if candidate.error not in method_errors and not repair_probe: raise Error(candidate.error)
        blocked = install.prerequisites(spec, selected, target) if selected else 'No exact-version installation method is available.'
        if blocked and not allow_unavailable: raise Error(blocked)
    effective = current.version if keep else target
    action = ('keep' if keep else 'install' if current is None else
              'downgrade' if version_tuple(current.version) and version_tuple(current.version) > version_tuple(effective) else
              'upgrade' if version_tuple(current.version) and version_tuple(current.version) < version_tuple(effective) else 'reinstall')
    try: destination = str(current.path if keep else install.destination(spec, selected))
    except Error: destination = None
    from agent_manager.providers.installation.software import fingerprint
    return dict(provider=manager.provider, kind=kind, targetVersion=effective, method=selected, requestedMethod=method,
                policyRevision=revision(), terminalPolicyRevision=terminal_policy.revision(), recipeRevision=releases.recipe_revision(),
                keepExisting=keep, versionTarget=selected_version, reinstall=bool(reinstall), destination=destination, action=action,
                existingVersion=current.version if current else None, existingPath=str(current.path) if current else None,
                existingFingerprint=fingerprint(current.path) if current else None, configuredExecutable=manager.config['executable'],
                host=dict(system=install.platform.system(), architecture=install.platform.machine().lower()),
                continuation=releases.feature(manager.provider, effective), blockedReason=blocked)


def validate(manager, plan, *, candidate=None):
    from agent_manager.manager import Manager
    manager = Manager(manager.provider, config=manager.config_path, root=manager.root.parent)
    current = resolve(manager, plan['kind'], method=plan.get('requestedMethod'), version_target=plan.get('versionTarget'),
                      reinstall=plan.get('reinstall', False), candidate=candidate)
    if current != plan:
        raise Error('Provider installation or release policy changed. Refresh setup before retrying.')
    return current


def execute(manager, plan, *, yes=True, progress=None):
    validate(manager, plan)
    if plan['keepExisting']:
        print(f'Keeping compatible {plan["targetVersion"]}; Agent Manager recommends {terminal_policy.recommended(manager.provider)}.')
        install.InstallationProgress(progress, plan['method']).report('complete', existing=True)
        return 0
    spec = install.catalog()[manager.provider]

    def recheck(found):
        observed = update.candidate(manager.provider, spec, manager.config_path, manager.root.parent, found=found)
        validate(manager, plan, candidate=observed)
        return observed.installation

    result = install.perform(manager.provider, spec, plan['method'], plan['targetVersion'],
                             bool(plan['existingPath']) or plan['reinstall'] or plan['kind'] == 'repair', yes,
                             update_check=recheck, progress=progress, installation_plan=plan,
                             root=manager.root.parent)
    if result == 0 and plan['kind'] == 'install' and not plan['existingPath']:
        from agent_manager.providers.installation.software import state_path
        state_path(manager).unlink(missing_ok=True)
    return result


def receipt_metadata(version, plan=None):
    plan = plan or {}
    selection = plan.get('versionTarget') or ('installed' if plan.get('kind') == 'repair' else 'recommended' if plan else version)
    return dict(requestedVersion=version, resolvedVersion=version, requestedSelector=selection,
                policyRevision=plan.get('policyRevision') or revision(),
                terminalPolicyRevision=plan.get('terminalPolicyRevision') or terminal_policy.revision(),
                recipeRevision=plan.get('recipeRevision') or releases.recipe_revision())


def describe(plan):
    print(f'Action: {plan["action"]}' + (f' ({plan["existingVersion"]} -> {plan["targetVersion"]})' if plan['existingVersion'] else ''))
    print(f'Version: {plan["versionTarget"] or "default"} -> {plan["targetVersion"]}')
    print(f'Method: {plan["method"] or "existing unmanaged installation"}; destination: {plan["destination"] or "unavailable"}')
    feature = plan['continuation']
    if feature:
        status = f'compatible ({feature["verification"]})' if feature['available'] else 'unavailable'
        print('Cross-environment continuation: ' + status)
        if not feature['available'] and feature.get('reason'): print('  ' + feature['reason'])
        if feature['available'] and feature['prerequisites']: print('  Requires: ' + ', '.join(feature['prerequisites']))
    else: print('Cross-environment continuation: unavailable')
    if plan['blockedReason']: print('Blocked: ' + plan['blockedReason'])
