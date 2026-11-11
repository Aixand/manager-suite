"""Windows installation logic uses isolated files and stubbed vendor processes."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from agent_manager.providers.installation import engine as install, windows as windows
from agent_manager.errors import Error


class WindowsProviderInstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        for stub in [patch('pathlib.Path.home', return_value=self.home),
                     patch('platform.machine', return_value='AMD64'),
                     patch.dict(os.environ, LOCALAPPDATA=str(self.home / 'local'), AGENT_MANAGER_SKIP_PATH_REGISTRATION='1'),
                     patch('agent_manager.providers.installation.engine.state_root', return_value=self.home / 'state'),
                     patch('agent_manager.providers.installation.engine.discover', return_value=[]),
                     patch('agent_manager.providers.installation.windows.prerequisites', return_value=None)]:
            stub.start()
            self.addCleanup(stub.stop)

    def inspect(self, spec, path):
        return install.Installation(path, 'installer', spec['recommended_version'],
                                    None if path.read_bytes() == b'good' else 'invalid executable')

    def test_opencode_uses_official_architecture_archive_and_ignores_archive_paths(self):
        urls = []
        def download(url, target, reporter, **kwargs):
            urls.append(url)
            with zipfile.ZipFile(target, 'w') as bundle:
                bundle.writestr('../../opencode.exe', b'good')
                bundle.writestr('../../do-not-extract.txt', b'never extract')
        with patch.object(windows, 'download', side_effect=download), patch.object(install, 'inspect', side_effect=self.inspect):
            windows.perform('opencode', install.catalog()['opencode'], 'installer', None, False, True)
        self.assertEqual(urls, ['https://opencode.ai/files/bin/2.2.5/opencode-windows-x64.zip'])
        self.assertEqual((self.home / '.opencode/bin/opencode.exe').read_bytes(), b'good')
        self.assertFalse((self.home / 'do-not-extract.txt').exists())
        self.assertTrue((self.home / 'state/installations/opencode.json').exists())

    def test_failed_download_verification_keeps_existing_executable(self):
        expected = self.home / '.opencode/bin/opencode.exe'
        expected.parent.mkdir(parents=True)
        expected.write_bytes(b'old executable')
        found = install.Installation(expected, 'installer', '2.0.0', None)
        def download(url, target, reporter, **kwargs):
            with zipfile.ZipFile(target, 'w') as bundle:
                bundle.writestr('opencode.exe', b'bad')
        with patch.object(install, 'discover', return_value=[found]), patch.object(windows, 'download', side_effect=download), \
                patch.object(install, 'inspect', side_effect=self.inspect):
            with self.assertRaisesRegex(Error, 'version check'):
                windows.perform('opencode', install.catalog()['opencode'], 'installer', None, True, True)
        self.assertEqual(expected.read_bytes(), b'old executable')
        self.assertFalse((self.home / 'state/installations/opencode.json').exists())

    def test_codex_and_claude_execute_downloaded_script_without_command_interpolation(self):
        for provider in ['codex', 'claude']:
            with self.subTest(provider=provider):
                spec = install.catalog()[provider]
                expected = windows.destination(spec)
                def download(url, target, reporter, **kwargs):
                    self.assertEqual(url, windows.URLS[provider])
                    target.write_bytes(b'param([string]$Release) # vendor installer')
                def run(args, **kwargs):
                    self.assertIn('-File', args)
                    self.assertNotIn('-Command', args)
                    self.assertTrue(Path(args[args.index('-File') + 1]).is_file())
                    if provider == 'codex':
                        self.assertEqual(kwargs['env']['CODEX_NON_INTERACTIVE'], '1')
                        self.assertEqual(kwargs['env']['CODEX_INSTALL_DIR'], str(expected.parent))
                    else:
                        self.assertEqual(args[-1], spec['recommended_version'])
                    expected.parent.mkdir(parents=True, exist_ok=True)
                    expected.write_bytes(b'good')
                with patch.object(windows, 'download', side_effect=download), patch.object(install, 'run_checked', side_effect=run), \
                        patch.object(install, 'inspect', side_effect=self.inspect):
                    windows.perform(provider, spec, 'installer', spec['recommended_version'], False, True)
                self.assertTrue(expected.is_file())

    def test_download_and_redirect_refuse_plain_http(self):
        with self.assertRaisesRegex(Error, 'HTTPS'):
            windows.download('http://example.com/provider', self.home / 'file', None)
        with self.assertRaisesRegex(Error, 'HTTPS'):
            windows.HTTPSRedirect().redirect_request(None, None, 302, '', {}, 'http://example.com/provider')

    def test_antigravity_routes_through_verified_pinned_artifacts(self):
        spec = install.catalog()['antigravity']
        expected = windows.destination(spec)
        self.assertEqual(expected, self.home / 'local/agy/bin/agy.exe')
        with patch('agent_manager.providers.installation.artifacts.perform', return_value=0) as artifact, patch.object(windows, 'download') as download:
            windows.perform('antigravity', spec, 'installer', None, False, True)
            self.assertEqual(artifact.call_args.args[2], '1.4.12')
            download.assert_not_called()

    def test_wrong_release_in_a_valid_archive_keeps_existing_software(self):
        expected = self.home / '.opencode/bin/opencode.exe'
        expected.parent.mkdir(parents=True)
        expected.write_bytes(b'working')
        found = install.Installation(expected, 'installer', '2.1.4', None)
        def download(url, target, reporter, **kwargs):
            with zipfile.ZipFile(target, 'w') as bundle:
                bundle.writestr('opencode.exe', b'valid but wrong release')
        with patch.object(install, 'discover', return_value=[found]), patch.object(windows, 'download', side_effect=download), \
                patch.object(install, 'inspect', return_value=install.Installation(expected, 'installer', '2.3.0', None)):
            with self.assertRaisesRegex(Error, 'version check'):
                windows.perform('opencode', install.catalog()['opencode'], 'installer', None, True, True)
        self.assertEqual(expected.read_bytes(), b'working')


if __name__ == '__main__':
    unittest.main()
