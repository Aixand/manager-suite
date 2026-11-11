"""SDK compatibility and architectural boundaries for the incremental refactor."""
import ast
import importlib
import inspect
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import uuid

from mcp_manager import Manager
from mcp_manager.storage.records import read, write

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'src/mcp_manager'


class ArchitectureTests(unittest.TestCase):
    def test_existing_sdk_signatures_and_async_operations_are_preserved(self):
        expected = json.loads((ROOT / 'tests/fixtures/sdk_signatures.json').read_text())
        actual = {
            name: re.sub(r'<object object at 0x[0-9a-f]+>', 'AUTO', str(inspect.signature(method)))
            for name, method in inspect.getmembers(Manager, inspect.isfunction)
            if not name.startswith('__')
        }
        self.assertEqual(actual, expected)
        for name in ('runtime_capabilities', 'runtime_status', 'reconnect'):
            self.assertTrue(inspect.iscoroutinefunction(getattr(Manager, name)))
        self.assertEqual(Manager.__bases__, (object,))

    def test_domain_modules_do_not_import_cli_facade_or_compatibility_modules(self):
        forbidden = {'mcp_manager.manager', 'mcp_manager.interfaces', 'mcp_manager.cli',
                     'mcp_manager.managed_cli', 'mcp_manager.managed', 'mcp_manager.runtime',
                     'mcp_manager._managed', 'mcp_manager._managed_view',
                     'mcp_manager._storage', 'mcp_manager._platform', 'mcp_manager._codec'}
        for area in ('library', 'storage', 'diagnostics', 'preparation', 'live', 'jobs', 'sources', 'installations'):
            for path in (PACKAGE / area).rglob('*.py'):
                package = 'mcp_manager.' + '.'.join(path.relative_to(PACKAGE).parts[:-1])
                for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                    modules = []
                    if isinstance(node, ast.Import):
                        modules = [item.name for item in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        module = '.' * node.level + (node.module or '')
                        module = importlib.util.resolve_name(module, package) if node.level else module
                        modules = [module]
                        # Also inspect `from mcp_manager import manager` forms.
                        modules.extend(module + '.' + alias.name for alias in node.names)
                    for module in modules:
                        with self.subTest(path=path.relative_to(PACKAGE), module=module):
                            self.assertFalse(any(module == item or module.startswith(item + '.')
                                                 for item in forbidden), module)

    def test_historical_module_exports_share_canonical_implementations(self):
        aliases = [
            ('managed', 'library.backend', 'Backend'),
            ('_managed', 'library.reconciliation', 'Managed'),
            ('_managed', 'library.reconciliation', 'MISSING'),
            ('_managed_view', 'library.views', 'view'),
            ('_codec', 'storage.codec', 'digest'),
            ('artifacts', 'library.artifacts', 'capture'),
            ('runtime', 'live.service', 'reconnect'),
            ('runtime', 'live.contracts', 'McpConnector'),
            ('cli', 'interfaces.cli.application', 'main'),
            ('managed_cli', 'interfaces.cli.managed', 'dispatch'),
            ('jobs', 'jobs.service', 'start'),
        ]
        for legacy, canonical, name in aliases:
            with self.subTest(legacy=legacy, name=name):
                self.assertIs(getattr(importlib.import_module('mcp_manager.' + legacy), name),
                              getattr(importlib.import_module('mcp_manager.' + canonical), name))

    def test_legacy_and_canonical_worker_entrypoints_read_existing_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for module in ('mcp_manager.jobs', 'mcp_manager.jobs.executor'):
                identifier = str(uuid.uuid4())
                job_dir = root / 'jobs' / identifier
                write(job_dir / 'request.json', {'tools': []})
                write(job_dir / 'status.json', {'id': identifier, 'status': 'queued'})
                result = subprocess.run([sys.executable, '-m', module, str(root), identifier],
                                        cwd=root, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stderr, '')
                self.assertEqual(read(job_dir / 'status.json')['status'], 'completed')

    def test_legacy_worker_function_remains_callable_after_repeated_use(self):
        from mcp_manager import jobs
        with tempfile.TemporaryDirectory() as directory:
            for _ in range(2):
                identifier = str(uuid.uuid4())
                job_dir = Path(directory) / 'jobs' / identifier
                write(job_dir / 'request.json', {'tools': []})
                write(job_dir / 'status.json', {'id': identifier, 'status': 'queued'})
                jobs.worker(directory, identifier)
                self.assertEqual(read(job_dir / 'status.json')['status'], 'completed')
