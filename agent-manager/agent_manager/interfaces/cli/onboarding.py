"""Interactive setup composed from existing installation and profile operations."""
import shutil

from agent_manager.errors import Error
from agent_manager.interfaces.cli import tui


def select(manager, *, picker=None):
    """Capture a stable target without requiring a successful budget probe."""
    picker = picker or tui.pick
    from agent_manager.profiles.listing import list_profiles
    rows = list_profiles(manager)
    if not rows: return None
    rows.sort(key=lambda row: (not row['preferred'], row['profile'].casefold()))
    selected = 0 if len(rows) == 1 else picker(f'Choose a {manager.provider} profile', ['Profile', 'Login', 'Native home'],
        [(row['profile'], row['login']['state'], row['home']) for row in rows],
        'Select a profile to launch or sign in')
    if selected is None: return None
    target = manager.resolve_profile(rows[selected]['profile'])
    if target.profile_id != rows[selected]['profile_id'] or target.environment_id != rows[selected]['environment_id']:
        raise Error('Profiles changed during selection. Retry start.')
    return target


def authenticate(manager, target, *, picker=None):
    picker = picker or tui.pick
    from agent_manager.providers.readiness import login_observation
    status = login_observation(manager, target, refresh=True)
    if status['state'] == 'signed-in': return True
    choices = [('Sign in', 'Open native authentication'), ('Launch', 'Continue in the native application')]
    selected = picker(f'{manager.provider}: {target}', ['Action', 'Details'], choices,
        'Sign-in required' if status['state'] == 'signed-out' else 'Sign-in status could not be verified')
    if selected is None: return False
    if selected == 1: return True
    if manager.login(target) != 0:
        raise Error(f'Sign-in did not complete. Retry with agent {manager.provider} login {target}')
    status = manager.login_status(target)
    if status['state'] == 'signed-out':
        raise Error('The provider still reports signed out. Retry native login.')
    return True


def setup(manager, *, picker=None, prompt=None):
    tui.require_terminal()
    picker, prompt = picker or tui.pick, prompt or tui.prompt
    from agent_manager.providers.installation import software, engine
    from agent_manager.providers.readiness import setup_state
    initial = setup_state(manager)
    if not initial['enabled']:
        selected = picker(f'Set up {manager.provider}', ['Action', 'Details'],
            [('Enable provider', 'Allow this provider to launch')])
        if selected is None: return None
        plan = software.plan(manager)
        software.perform(manager, 'enable', revision=plan['revision'], confirmed=True)
    if not initial['executable']:
        if manager.provider not in engine.catalog():
            raise Error(f'No managed installer is available for {manager.provider}. Install {manager.config["executable"]} and retry onboarding.')
        selected = picker(f'Set up {manager.provider}', ['Action', 'Details'],
            [('Install CLI', 'Use the official installer')])
        if selected is None: return None
        from agent_manager.interfaces.cli.parser import Parser
        code = engine.command(engine.parser(Parser).parse_args([manager.provider]),
            config=manager.config_path, home=manager.root.parent)
        if code: raise Error('Provider installation did not complete')
        if not shutil.which(manager.config['executable']):
            raise Error('CLI is not on PATH. Follow the installer instructions, then retry onboarding.')
    while True:
        existing = bool(manager.state()['profiles'])
        actions = ([('Use existing profile', 'Choose a configured profile')] if existing else []) + [
            ('Create profile', 'Create a fresh native home'), ('Connect profile', 'Use an existing native home')]
        selected = picker(f'Set up {manager.provider}', ['Action', 'Details'], actions)
        if selected is None: return None
        if existing and selected == 0:
            return select(manager, picker=picker)
        connect = selected - int(existing) == 1
        name = prompt(f'Set up {manager.provider}', 'Profile name', default='main')
        if name is None: return None
        try:
            if connect:
                directory = prompt('Connect profile', 'Existing native home directory')
                if directory is None: return None
                if not directory: raise Error('Enter an existing native home directory')
                manager.connect_profile(name, directory)
            else:
                manager.new_profile(name)
            if not existing: manager.set_default(name)
            return manager.resolve_profile(name)
        except (Error, ValueError, OSError) as error:
            if picker('Profile could not be configured', ['Action', 'Details'], [('Retry', str(error))]) is None:
                return None


def command(manager):
    target = setup(manager)
    if target is None or not authenticate(manager, target): return 0
    selected = tui.pick('Setup complete', ['Action', 'Details'], [
        ('Launch', f'Open {manager.provider} profile {target}'), ('Finish', f'Run later: agent {manager.provider} run {target}')])
    if selected == 0: return manager._run_native(target, [])
    print(f'Configured {manager.provider} profile "{target}". Run: agent {manager.provider} run {target}')
    return 0


def global_command(*, config=None, home=None):
    tui.require_terminal()
    from agent_manager.manager import Manager
    from agent_manager.providers.config import load_registry
    registry = load_registry(config)
    providers = list(registry.settings)
    while True:
        selected = tui.pick('Agent onboarding', ['Provider', 'Details'],
            [(provider, registry.settings[provider].get('label', provider)) for provider in providers] + [('Finish', 'Exit onboarding')])
        if selected is None or selected == len(providers): return 0
        code = command(Manager(providers[selected], config=config, root=home))
        if code: return code
