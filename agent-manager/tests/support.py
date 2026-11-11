"""Disposable native-profile fixtures; no real providers, logins or daemons."""
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from agent_manager import Client, Manager
from agent_manager.runtime.runs import update_run


def native_manifest(identifier='test', aliases=(), *, home='~/.example', environment='TEST_AGENT_HOME'):
    return f'''schema_version = 3
id = {json.dumps(identifier)}
label = "Fixture provider"
description = "Disposable native provider"
aliases = {json.dumps(list(aliases))}
[terminal]
executable = "fixture-provider"
[terminal.home]
strategy = "environment-directory"
environment = {json.dumps(environment)}
default_path = {json.dumps(str(home))}
binding = "environment"
settings_scope = "environment"
history_scope = "environment"
[terminal.login]
args = ["login"]
[terminal.sessions]
strategy = "native-picker"
resume_args = ["--resume"]
cross_environment_resume = "disabled"
[terminal.models]
strategy = "native-argument"
'''


PROVIDER = '''
import json, os, sys, time
from pathlib import Path
home = Path(os.environ['TEST_AGENT_HOME'])
marker = home / 'native-login-marker'
arguments = sys.argv[1:]
if arguments[:1] == ['login']:
    if os.environ.get('FAKE_LOGIN_LOG'):
        Path(os.environ['FAKE_LOGIN_LOG']).write_text(json.dumps(arguments))
    if os.environ.get('FAKE_LOGIN_EXIT'):
        sys.exit(int(os.environ['FAKE_LOGIN_EXIT']))
    marker.write_text('native-sign-in')
else:
    (home / 'observed.json').write_text(json.dumps({'args': arguments,
        'home': str(home), 'cwd': str(Path.cwd())}))
    if os.environ.get('FAKE_RUN_SLEEP'):
        time.sleep(float(os.environ['FAKE_RUN_SLEEP']))
    sys.exit(int(os.environ.get('FAKE_RUN_EXIT', '0')))
'''


class ProfileFixture:
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='agent-profile-test-')
        self.addCleanup(temporary.cleanup)
        self.temp = temporary
        self.base = Path(temporary.name)
        self.fake = self.base / 'provider'
        self.fake.write_text('#!' + sys.executable + '\n' + PROVIDER)
        self.fake.chmod(0o700)
        self.config = self.base / 'providers.toml'
        self.config.write_text('[providers.test]\nexecutable=' + json.dumps(str(self.fake))
            + '\nrun_args=["--configured"]\n')
        definitions = self.base / 'providers'; definitions.mkdir()
        (definitions / 'test.toml').write_text(native_manifest(home=self.base / 'native'))
        self.manager = Manager('test', self.config, self.base / 'homes')

    def create(self, profile='work'):
        self.manager.new_profile(profile)
        self.assertEqual(self.manager.login(profile), 0)


class FakeRuntime:
    connection_options = {'home': '/tmp/unused-profile-runtime'}

    async def connect(self):
        pass


class ManagedFixture(ProfileFixture):
    def client(self):
        return Client('test', config=self.config, root=self.base / 'homes', runtime=FakeRuntime())

    async def launch(self, client=None, **options):
        def accepted(manager, run_id):
            update_run(manager, run_id, session_id='session-' + run_id, status='running')
        with patch('agent_manager.interfaces.sdk.launch_worker', side_effect=accepted):
            return await (client or self.client()).spawn(profile='work', **options)
