"""Pinned native artifacts are verified before replacing installed software."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from agent_manager.providers.installation import engine as install, artifacts as provider_artifacts
from agent_manager.providers import policy as provider_policy
from agent_manager.errors import Error


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binary = self.root / 'bin/agy'
        self.binary.parent.mkdir()
        self.binary.write_bytes(b'old working CLI')
        self.record = self.root / 'state/installations/antigravity.json'
        self.record.parent.mkdir(parents=True)
        self.record.write_text('{"version":"1.4.7"}')
        self.old_record = self.record.read_bytes()
        self.spec = install.catalog()['antigravity']
        self.payload = self.archive()
        self.version = '1.4.12'
        self.source = dict(version='1.4.12', url='https://vendor.example/1.4.12/cli.tar.gz', sha512=hashlib.sha512(self.payload).hexdigest())
        for stub in [patch.object(provider_policy, 'artifact', side_effect=lambda *a: self.source),
                patch.object(install, 'state_root', return_value=self.root / 'state'),
                patch.object(install, 'native_binary', side_effect=lambda *a: self.binary),
                patch.object(install, 'discover', side_effect=lambda *a: [install.Installation(self.binary, 'installer', '1.4.7', None)]),
                patch.object(install, 'inspect', side_effect=lambda spec, path: install.Installation(path, 'installer', self.version, None)),
                patch.object(provider_artifacts.urllib.request, 'build_opener')]:
            started = stub.start()
            if stub.attribute == 'build_opener':
                self.opener = started
            self.addCleanup(stub.stop)
        self.opener.return_value.open.side_effect = self.response

    def response(self, *args, **kwargs):
        stream = io.BytesIO(self.payload)
        stream.headers = {'Content-Length': str(len(self.payload))}
        return stream

    def archive(self, *, duplicate=False, link=False):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as archive:
            for name in ['nested/antigravity', *(['another/antigravity'] if duplicate else [])]:
                row = tarfile.TarInfo(name)
                if link:
                    row.type, row.linkname = tarfile.SYMTYPE, '/outside'
                    archive.addfile(row)
                else:
                    row.size = len(b'verified new CLI')
                    archive.addfile(row, io.BytesIO(b'verified new CLI'))
        return stream.getvalue()

    def perform(self):
        return provider_artifacts.perform('antigravity', self.spec, '1.4.12', True)

    def test_checksum_and_version_are_checked_before_publishing(self):
        self.assertEqual(self.perform(), 0)
        self.assertEqual(self.binary.read_bytes(), b'verified new CLI')
        record = json.loads(self.record.read_bytes())
        self.assertEqual(record['version'], '1.4.12')
        self.assertEqual(record['artifact']['sha512'], self.source['sha512'])
        self.assertEqual(record['policyRevision'], provider_policy.revision())

    def test_checksum_or_version_failure_preserves_executable_and_receipt(self):
        for failure in ['checksum', 'version']:
            with self.subTest(failure=failure):
                self.source['sha512'] = '0' * 128 if failure == 'checksum' else hashlib.sha512(self.payload).hexdigest()
                self.version = '1.4.9'
                with self.assertRaises(Error):
                    self.perform()
                self.assertEqual(self.binary.read_bytes(), b'old working CLI')
                self.assertEqual(self.record.read_bytes(), self.old_record)

    def test_archive_links_and_duplicate_executables_are_rejected(self):
        for options in [dict(link=True), dict(duplicate=True)]:
            self.payload = self.archive(**options)
            self.source['sha512'] = hashlib.sha512(self.payload).hexdigest()
            with self.assertRaisesRegex(Error, 'one CLI executable'):
                self.perform()
            self.assertEqual(self.binary.read_bytes(), b'old working CLI')

    def test_windows_download_verifies_the_exe_before_replacement(self):
        self.binary = self.root / 'bin/agy.exe'
        self.binary.write_bytes(b'old working CLI')
        self.payload = b'verified new CLI'
        self.source.update(url='https://vendor.example/1.4.12/cli.exe', sha512=hashlib.sha512(self.payload).hexdigest())
        with patch('platform.system', return_value='Windows'), patch('agent_manager.providers.installation.windows.add_user_path') as register:
            self.assertEqual(self.perform(), 0)
            register.assert_called_once_with(self.binary.parent)
