"""Bounded native feature probes in disposable, unsigned-in homes.

No production pins or account files are changed. An accepted metadata response
qualifies that wire parser; an authentication error does not qualify account quota.
Run from the project root: python tests/manual/qualify_features.py --provider opencode
"""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import tempfile
from unittest.mock import patch

from agent_manager import Manager
from agent_manager.budgets.models import BudgetError
from agent_manager.models.schema import ModelError
from agent_manager.providers.installation import engine


def probe(provider, features, timeout):
    spec = engine.catalog()[provider]
    executable = shutil.which(spec['executable'])
    if not executable: return dict(provider=provider, status='missing-cli')
    with tempfile.TemporaryDirectory(prefix='agent-feature-probe-') as directory:
        root = Path(directory)
        user = root / 'user'; user.mkdir()
        config = root / 'providers.toml'; config.write_text('')
        environment = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT', 'SystemRoot',
            'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'USER', 'LOGNAME') if key in os.environ}
        environment.update(HOME=str(user), USERPROFILE=str(user), DBUS_SESSION_BUS_ADDRESS='disabled:', SSH_AUTH_SOCK='')
        environment.update({'XDG_' + key + '_HOME': str(user / key.lower()) for key in ('CONFIG', 'DATA', 'STATE', 'CACHE')})
        with patch.dict(os.environ, environment, clear=True):
            manager = Manager(provider, config=config, root=root / 'state')
            manager.config['executable'] = executable
            found = engine.inspect(spec, Path(executable))
            if not found.version: return dict(provider=provider, status='unknown-version')
            manager.new_profile('probe')
            home = manager.home('probe')
            results = {}
            for feature in features:
                policy = (manager.provider_spec.models.discovery if feature == 'models' else
                    manager.provider_spec.integrations if feature == 'integrations' else manager.provider_spec.budget)
                if policy.adapter == 'disabled':
                    results[feature] = dict(status='unsupported-route')
                    continue
                policy = replace(policy, versions=(found.version,), timeout=timeout)
                try:
                    if feature == 'models':
                        from agent_manager.models.adapters import read
                        values, _, _ = read(manager, home, root, policy)
                    elif feature == 'integrations':
                        import time
                        from agent_manager.integrations import opencode, schema
                        native, bridge, _ = opencode.read(manager, home, root, policy)
                        normalized = schema.validate_data(schema.normalize(native, bridge, policy, time.time()))
                        if not normalized['bridge_available']:
                            results[feature] = dict(status='not-qualified', reason='bridge-unavailable')
                            continue
                        values = normalized['integrations']
                    else:
                        from agent_manager.budgets.adapters import read
                        values = read(manager, home, policy)
                    results[feature] = dict(status='accepted' if values else 'accepted-empty', count=len(values))
                except (BudgetError, ModelError) as error:
                    results[feature] = dict(status='not-qualified', reason=error.code)
            return dict(provider=provider, cli_version=found.version, verification='native-no-auth',
                home='disposable', credentials='environment-cleared', results=results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', choices=list(engine.catalog()), required=True)
    parser.add_argument('--features', nargs='+', choices=('models', 'budget', 'integrations'), default=['models', 'budget'])
    parser.add_argument('--timeout', type=int, choices=range(1, 31), default=8, metavar='SECONDS')
    args = parser.parse_args()
    print(json.dumps(probe(args.provider, args.features, args.timeout), indent=2))


if __name__ == '__main__': main()
