"""Standalone distribution and dependency boundaries."""
import ast
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_EXTERNAL = {'github_source', 'aixand_filesystem'}


class StandaloneTests(unittest.TestCase):
    def test_package_imports_only_stdlib_and_declared_shared_library(self):
        # Fail on new sibling dependencies, including literal dynamic imports.
        allowed = sys.stdlib_module_names | ALLOWED_EXTERNAL | {'mcp_manager'}
        for path in (ROOT / 'src/mcp_manager').rglob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                imports = []
                if isinstance(node, ast.Import):
                    imports = [item.name for item in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    imports = [node.module]
                elif (isinstance(node, ast.Call) and node.args
                      and isinstance(node.args[0], ast.Constant)
                      and isinstance(node.args[0].value, str)
                      and ((isinstance(node.func, ast.Name) and node.func.id == '__import__')
                           or (isinstance(node.func, ast.Attribute) and node.func.attr == 'import_module'))):
                    imports = [node.args[0].value]
                for module in imports:
                    with self.subTest(path=path.relative_to(ROOT), module=module):
                        self.assertIn(module.split('.')[0], allowed)

    def test_copied_sources_work_without_site_packages_or_sibling_checkout(self):
        import github_source
        import aixand_filesystem
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for source in (ROOT / 'src/mcp_manager', Path(github_source.__file__).parent,
                           Path(aixand_filesystem.__file__).parent):
                shutil.copytree(source, root / source.name, ignore=shutil.ignore_patterns('__pycache__'))
            script = '''
import importlib.abc, json, sys
sys.path.insert(0, sys.argv[1])
class BlockOtherProjects(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'agent_manager', 'server_manager', 'prompt_manager',
                                     'skill_manager', 'runner', 'carrier', 'suite', 'pathspec',
                                     'orchestration_mcp', 'resource_sources'}:
            raise AssertionError('Forbidden dependency: ' + fullname)
sys.meta_path.insert(0, BlockOtherProjects())
from mcp_manager import Manager
from mcp_manager.cli import main
manager = Manager(sys.argv[2])
assert manager.catalog() == []
assert not manager.home.exists()
created = manager.add('docs', {'command': sys.executable})
assert manager.resolve(['docs'], sys.argv[1])['servers']['docs']
manager.ensure_registered('local', {'command': sys.executable})
assert manager.inspect_managed('local', 'default', sys.argv[1])['servers']
assert main(['--home', sys.argv[2], 'managed', 'list', '--json']) == 0
'''
            result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', script,
                                     str(root), str(root / 'store')],
                                    cwd=root, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(len(json.loads(result.stdout)), 1)

    def test_development_entrypoint_works_in_isolated_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'dev.py'
            shutil.copy2(ROOT / 'dev.py', script)
            result = subprocess.run([sys.executable, '-I', '-S', str(script), 'help'],
                                    cwd=directory, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('build', result.stdout)
