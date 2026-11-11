"""Standalone distribution and execution without resource-manager imports."""
import ast
from pathlib import Path
import subprocess
import sys
import textwrap
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OptionalManagersTests(unittest.TestCase):
    def test_metadata_and_source_have_no_resource_manager_dependencies(self):
        project = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']
        forbidden = ('aixand-prompt-manager', 'aixand-skill-manager', 'aixand-mcp-manager', 'carrier-mcp')
        for dependency in project['dependencies'] + [d for group in project['optional-dependencies'].values() for d in group]:
            self.assertFalse(dependency.startswith(forbidden), dependency)
        for source in (ROOT / 'agent_manager').rglob('*.py'):
            for node in ast.walk(ast.parse(source.read_text())):
                imports = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ''] if isinstance(node, ast.ImportFrom) else []
                self.assertFalse(any(name.split('.')[0] in {'prompt_manager', 'skill_manager', 'mcp_manager', 'carrier'} for name in imports), str(source))
        self.assertIn('mcp', project['optional-dependencies'])

    def test_core_launch_and_target_discovery_with_blocked_resource_managers(self):
        script = '''
import importlib.abc, json, sys, tempfile
from pathlib import Path
sys.path.insert(0, ROOT)
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'prompt_manager','skill_manager','mcp_manager','carrier'}:
            raise AssertionError('Resource manager imported: ' + fullname)
sys.meta_path.insert(0, Block())
from agent_manager import Manager
from agent_manager.interfaces.cli.dispatch import execute
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    config = root / 'providers.toml'
    config.write_text('')
    manager = Manager('codex', config=config, root=root/'state')
    manager.new_profile('work')
    manager.set_default('work')
    identifier = manager.resolve_profile('work').environment_id
    state = manager.state()
    state['mcp_bindings'] = {identifier: {'schema': 'legacy-unavailable', 'servers': ['missing']}}
    manager.save(state)
    before = manager.state_path.read_bytes()
    native = manager.home('work')/'config.toml'
    native.write_text('[mcp_servers.native]\\ncommand="existing"\\n')
    old_config = native.read_bytes()
    manager._invoke_native = lambda home, args: 0
    assert manager._run_native('work', ['--yolo']) == 0
    assert native.read_bytes() == old_config
    assert manager.state()['mcp_bindings'] == state['mcp_bindings']
    assert manager.agent_targets()[0]['environment_id'] == identifier
    assert not (manager.root/'resources').exists()
    assert not (manager.root/'native-instructions').exists()
    assert execute(['--config',str(config),'--home',str(root/'state'),'codex','target','list','--json']) == 0
'''
        result = subprocess.run([sys.executable, '-I', '-c', 'ROOT = ' + repr(str(ROOT)) + '\n' + textwrap.dedent(script)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
