"""Host safety and failure reporting for the opt-in vendor installation check."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.manual import installation_containers as containers


class ContainerInstallationChecks(unittest.TestCase):
    def dependency_sources(self, root):
        """Snapshot tests supply their inputs without assuming sibling checkouts."""
        arguments = []
        for option, name, module in (
                ('--runtime-source', 'aixand-lib-local-runtime', 'aixand_local_runtime'),
                ('--filesystem-source', 'aixand-lib-filesystem', 'aixand_filesystem')):
            source = root / name
            package = source / 'src' / module
            package.mkdir(parents=True)
            (package / '__init__.py').write_text('# Snapshot fixture\n')
            (source / 'pyproject.toml').write_text(
                f'[project]\nname = "{name}"\nversion = "0.1.0b0"\n')
            for filename in ('README.md', 'LICENSE'):
                (source / filename).write_text('Snapshot fixture\n')
            arguments.extend((option, str(source)))
        return arguments

    def test_snapshot_omits_hidden_state_bytecode_and_nonpackage_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, destination = root / 'package', root / 'snapshot'
            source.mkdir()
            for relative in ['__init__.py', 'definitions/provider.toml', 'policy.txt',
                             '.credentials/token.txt', '__pycache__/compiled.py', 'auth.json', '.env']:
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture')
            containers.copy_source(source, destination)
            files = {str(path.relative_to(destination)) for path in destination.rglob('*') if path.is_file()}
            self.assertEqual(files, {'__init__.py', 'definitions/provider.toml', 'policy.txt'})

    def test_snapshot_rejects_links_to_outside_files_or_package_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'package'
            source.mkdir()
            private = root / 'private.py'
            private.write_text('do not copy')
            (source / 'redirect.py').symlink_to(private)
            with self.assertRaisesRegex(RuntimeError, 'symlink'):
                containers.copy_source(source, root / 'snapshot')
            alias = root / 'alias'
            alias.symlink_to(source, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, 'symlink'):
                containers.copy_source(alias, root / 'snapshot')

    def test_prepare_only_never_invokes_podman_or_installs_software(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'results'
            dependencies = self.dependency_sources(Path(temporary))
            with patch.object(sys, 'argv', ['installation_containers.py', '--prepare-only', '--output', str(output), *dependencies]), \
                    patch.object(subprocess, 'run', side_effect=AssertionError('No runtime or vendor execution')), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(containers.main(), 0)
            result = json.loads((output / 'results.json').read_text())
            self.assertEqual(result['status'], 'prepared')
            self.assertEqual(result['cases'], [])
            self.assertEqual(len(result['selectedCases']), 5)
            self.assertTrue((output / 'context/Containerfile').is_file())
            self.assertTrue((output / 'context/aixand-lib-local-runtime/src/aixand_local_runtime/__init__.py').is_file())
            self.assertTrue((output / 'context/aixand-lib-filesystem/src/aixand_filesystem/__init__.py').is_file())
            self.assertFalse(any(path.name in ('.git', '.venv', '__pycache__') for path in (output / 'context').rglob('*')))

    def test_failed_preflight_is_blocked_and_never_builds_an_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'results'
            dependencies = self.dependency_sources(Path(temporary))
            failure = subprocess.CompletedProcess(['podman'], 125, '', 'cannot create namespace')
            with patch.object(sys, 'argv', ['installation_containers.py', '--output', str(output), *dependencies]), \
                    patch.object(os, 'geteuid', return_value=1000), \
                    patch.object(containers.shutil, 'which', return_value='/usr/bin/podman'), \
                    patch.object(subprocess, 'run', return_value=failure) as execute, \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(containers.main(), 1)
            self.assertEqual(execute.call_count, 1)
            self.assertEqual(execute.call_args.args[0], ['podman', 'info', '--format', 'json'])
            result = json.loads((output / 'results.json').read_text())
            self.assertEqual(result['status'], 'blocked')
            self.assertEqual(result['cases'], [])
            self.assertFalse((output / 'context').exists())

    def test_failed_container_cannot_be_reported_as_passed_and_is_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            case = dict(provider='codex', method='installer')
            def copy_findings(command, **kwargs):
                self.assertEqual(command[:2], ['podman', 'cp'])
                Path(command[-1]).write_text(json.dumps(dict(case, status='passed')))
                return subprocess.CompletedProcess(command, 0, '', '')
            with patch.object(containers, 'logged', return_value=1) as run, \
                    patch.object(subprocess, 'run', side_effect=copy_findings), \
                    patch.object(containers, 'cleanup', return_value=None) as cleanup, \
                    contextlib.redirect_stdout(io.StringIO()):
                result = containers.run_case('local-fixture', case, output, 5)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(cleanup.call_args.args[0][:4], ['podman', 'rm', '--force', '--ignore'])
            command = run.call_args.args[0]
            self.assertIn('--http-proxy=false', command)
            self.assertIn('--cap-drop=all', command)
            self.assertIn('--security-opt=no-new-privileges', command)
            self.assertFalse(any(arg in command for arg in ('--volume', '-v', '--mount', '--privileged', '--network=host')))

    def test_vendor_script_refuses_execution_on_the_host_before_creating_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / 'providers.toml'
            env = dict(os.environ, AGENT_MANAGER_CONFIG=str(config))
            env.pop('AGENT_INSTALL_SMOKE_CONTAINER', None)
            result = subprocess.run([sys.executable, str(containers.MANUAL / 'installation_smoke.py'), 'codex', 'installer'],
                                    env=env, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('requires its disposable container', result.stderr)
            self.assertFalse(config.exists())


if __name__ == '__main__':
    unittest.main()
