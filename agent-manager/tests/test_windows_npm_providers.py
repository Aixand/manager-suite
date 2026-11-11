"""Exercise npm entry points and provider launches without evaluating cmd shims."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_manager import Manager
from agent_manager.providers.installation import engine as install, software as provider_software, updates as update
from agent_manager.interfaces.cli.parser import Parser


NPM = r'''
const fs = require('node:fs'), path = require('node:path')
const prefix = process.env.TEST_NPM_PREFIX, args = process.argv.slice(2)
if (args[0] === 'prefix') { console.log(prefix); process.exit(0) }
const packages = { '@github/copilot': 'copilot' }
const package = args.at(-1).replace(/@[^@/]+$/, ''), name = packages[package]
if (!name) process.exit(1)
const destination = path.join(prefix, name + '.cmd')
if (args[0] === 'uninstall') {
  for (const suffix of ['', '.cmd', '.ps1']) fs.unlinkSync(path.join(prefix, name + suffix))
  process.exit(0)
}
const directory = path.join(prefix, 'node_modules', package)
fs.mkdirSync(directory, {recursive:true})
fs.writeFileSync(path.join(directory, 'package.json'), JSON.stringify({name:package, bin:{[name]:'cli.js'}}))
const banner = name === 'copilot' ? "GitHub Copilot CLI 1.2.3.\nRun 'copilot update' to check for updates." : name + ' 1.2.3'
fs.writeFileSync(path.join(directory, 'cli.js'), 'console.log(' + JSON.stringify(banner) + ')')
fs.writeFileSync(destination, 'This shim must never be evaluated & echo unsafe')
fs.writeFileSync(path.join(prefix, name), '#!/bin/sh\necho Unix launcher')
fs.writeFileSync(path.join(prefix, name + '.ps1'), 'This PowerShell shim must never be evaluated')
'''


@unittest.skipUnless(os.name == 'nt', 'Requires Windows npm entry point semantics')
class WindowsNpmProviderTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='npm providers 世界 ')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.node = shutil.which('node') or os.environ.get('AIXAND_TEST_NODE')
        if not self.node:
            self.skipTest('Node is required for the Windows provider fixture')
        self.prefix = self.root / 'npm user prefix'
        self.prefix.mkdir()
        self.npm = self.root / 'tools/npm.cmd'
        self.npm.parent.mkdir()
        self.npm.write_text('never execute the npm cmd shim')
        script = self.npm.parent / 'node_modules/npm/bin/npm-cli.js'
        script.parent.mkdir(parents=True)
        script.write_text(NPM)
        which = shutil.which
        def tool(name, *args, **kwargs):
            if name == 'npm':
                return str(self.npm)
            if name == 'node':
                return str(self.node)
            if name == 'brew':
                return None
            return which(name, *args, **kwargs)
        for stub in (patch('shutil.which', side_effect=tool), patch('pathlib.Path.home', return_value=self.root / 'home'),
                     patch.dict(os.environ, TEST_NPM_PREFIX=str(self.prefix), XDG_STATE_HOME=str(self.root / 'state'),
                                PATH=os.pathsep.join((str(self.prefix), str(self.root / 'empty-path'))), AIXAND_PROVIDER_BUNDLE_ROOT='')):
            stub.start()
            self.addCleanup(stub.stop)

    def seed_external(self):
        script = self.npm.parent / 'node_modules/npm/bin/npm-cli.js'
        subprocess.run([str(self.node), str(script), 'install', '-g', '@github/copilot@1.2.3'], check=True)

    def test_external_copilot_package_can_launch_but_is_not_managed(self):
        for provider in ('copilot',):
            with self.subTest(provider=provider):
                self.seed_external()
                spec = install.catalog()[provider]
                found = install.discover(spec)
                self.assertEqual(len(found), 1)
                self.assertEqual((found[0].method, found[0].version), ('npm', '1.2.3'))
                manager = Manager(provider, root=self.root / 'profiles')
                command = manager.launch_command(manager.native, [str(found[0].path), '--version'])
                result = subprocess.run(command, capture_output=True, text=True, check=True)
                self.assertIn('GitHub Copilot CLI 1.2.3.', result.stdout)
                updated = update.parser(Parser).parse_args([provider, '--yes'])
                with self.assertRaisesRegex(install.Error, 'externally'):
                    update.command(updated, home=self.root / 'profiles')
                current = provider_software.plan(manager)
                self.assertFalse(current['automatic'])
                self.assertEqual(current['method'], 'npm')
                with self.assertRaisesRegex(install.Error, 'managed externally'):
                    provider_software.perform(manager, 'uninstall', revision=current['revision'], confirmed=True)
                self.assertTrue(found[0].path.exists())

    def test_rejects_package_entrypoint_that_escapes_its_verified_package(self):
        self.seed_external()
        package = self.prefix / 'node_modules/@github/copilot'
        outside = self.root / 'outside.js'
        outside.write_text('process.exit(0)')
        (package / 'package.json').write_text(json.dumps({'name': '@github/copilot', 'bin': {'copilot': str(outside)}}))
        with self.assertRaisesRegex(install.Error, 'verified npm'):
            install.executable_command(self.prefix / 'copilot.cmd', ['--version'])

    def test_discovery_keeps_genuinely_separate_windows_installations(self):
        self.seed_external()
        other = self.root / 'another npm prefix'
        shutil.copytree(self.prefix, other)
        with patch.dict(os.environ, PATH=os.environ['PATH'] + os.pathsep + str(other)):
            found = install.discover(install.catalog()['copilot'])
        self.assertEqual(len(found), 2)
        self.assertEqual({item.path.parent for item in found}, {self.prefix, other})
        self.assertEqual(install.migration_error(found, 'npm'),
                         'Multiple installations found; resolve PATH conflicts before reinstalling')


if __name__ == '__main__':
    unittest.main()
