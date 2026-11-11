"""Version discovery accepts CLI banners without accepting partial versions."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_manager.providers.installation import engine as install


class ProviderVersionTests(unittest.TestCase):
    def test_inspects_copilot_sentence_ending_version_banner(self):
        banner = "GitHub Copilot CLI 1.0.103.\nRun 'copilot update' to check for updates.\n"
        with patch.object(install, 'output', return_value=banner), patch.object(install, 'identify_method', return_value='npm'):
            found = install.inspect(install.catalog()['copilot'], Path('copilot'))
        self.assertEqual(found.version, '1.0.103')
        self.assertIsNone(found.error)

    def test_preserves_other_provider_version_formats(self):
        for banner, expected in [('codex-cli 0.160.0', '0.160.0'), ('2.0.23\n', '2.0.23'),
                                 ('v0.43.0', '0.43.0'), ('agy 1.4.0', '1.4.0'),
                                 ('tool 1.2.3-beta.4.', '1.2.3-beta.4')]:
            with self.subTest(banner=banner):
                self.assertEqual(install.version_from_output(banner), expected)

    def test_rejects_missing_or_partial_versions(self):
        for banner in (None, '', 'unknown', 'tool 1.2', 'tool 1.2.3.4', 'tool 1.2.3.4.', 'tool 1.2.3bad'):
            with self.subTest(banner=banner):
                self.assertIsNone(install.version_from_output(banner))

    def test_external_discovery_accepts_sentence_punctuation(self):
        path = Path('unmanaged-provider').absolute()
        manager = SimpleNamespace(config={'executable': 'unmanaged-provider'}, provider='unmanaged-provider')
        with patch.object(install, 'provider_executable', return_value=path), \
                patch.object(install, 'output', return_value='Provider 1.2.3.\n'):
            found = install.inspect_external(manager)
        self.assertEqual(found.version, '1.2.3')
        self.assertIsNone(found.error)


if __name__ == '__main__':
    unittest.main()
