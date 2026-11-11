"""Exercise exported CLIs in clean terminals, without desktop runtime paths."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_manager import Manager
from agent_manager.providers.installation import engine as install, exports as commands, software as provider_software


@unittest.skipIf(os.name=='nt', 'POSIX executable fixtures; Windows is qualified with a native launcher and real Node')
class SharedCommandsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='provider-commands-')
        self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name)/'user with spaces'
        self.home.mkdir()
        self.root=self.home/'shared providers'
        self.application=self.home/'desktop/runtime'
        self.source=self.application/'node'
        (self.source/'bin').mkdir(parents=True)
        (self.source/'lib/node_modules/npm/bin').mkdir(parents=True)
        self.node=self.source/'bin/node'
        self.node.write_text('#!/bin/sh\nexec "$@"\n')
        self.node.chmod(0o755)
        (self.source/'lib/node_modules/npm/bin/npm-cli.js').write_text('fixture npm entry')
        self.environment=patch.dict(os.environ,HOME=str(self.home),SHELL='/bin/bash',XDG_STATE_HOME=str(self.home/'state'),
            AIXAND_PROVIDER_STORE=str(self.root),AIXAND_PROVIDER_BUNDLE_ROOT=str(self.application),AIXAND_LOCAL_HOME=str(self.home/'workspace'),
            AIXAND_PROVIDER_NODE_SOURCE=str(self.source),AIXAND_PROVIDER_USER_PATH='/usr/bin:/bin',AIXAND_SKIP_PATH_REGISTRATION='0',
            AIXAND_PROVIDER_COMMAND_POLICY='prefer',AIXAND_PROVIDER_COMMAND_POLICY_FILE='',PATH='/usr/bin:/bin')
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.config=self.home/'providers.toml'
        self.config.write_text('')
        self.manager=Manager('copilot',config=self.config,root=self.home/'accounts')
        self.account=self.home/'.copilot/login.json'
        self.account.parent.mkdir()
        self.account.write_text('existing-login-data')

    def package(self,provider='copilot',prefix=None,version='1.2.3'):
        spec=install.catalog()[provider]
        if prefix is None:
            entry=self.home/'.local/bin'/spec['executable']
        else:
            package=prefix/'lib/node_modules'/spec['npm_package']
            package.mkdir(parents=True,exist_ok=True)
            (package/'package.json').write_text(json.dumps(dict(name=spec['npm_package'],version=version,bin={spec['executable']:'cli.js'})))
            entry=package/'cli.js'
        entry.parent.mkdir(parents=True,exist_ok=True)
        entry.write_text('#!/usr/bin/python3\nimport json,os,sys\nif any(flag in sys.argv for flag in ("--version", "--binary-version")): print("'+provider+' '+version+'")\nelse:\n print(json.dumps({"args":sys.argv[1:],"cwd":os.getcwd(),"home":os.environ.get("COPILOT_HOME")}))\n sys.exit(7 if "--exit-seven" in sys.argv else 0)\n')
        entry.chmod(0o755)
        if prefix is None: return entry
        (prefix/'bin').mkdir(exist_ok=True)
        path=prefix/'bin'/spec['executable']
        path.symlink_to(entry)
        return path

    def prepare(self):
        self.root.mkdir(exist_ok=True)
        # Retain an existing owned runtime fixture; registration never provisions it.
        shutil.copytree(self.source, self.root/'node')
        marker=self.root/'node/runtime-receipt.json'
        marker.write_text(json.dumps(dict(schema=1,sha256=commands.digest(self.root/'node/bin/node'))))

    def test_readonly_inventory_does_not_create_software_or_modify_accounts(self):
        self.assertFalse(commands.status(self.manager)['ready'])
        self.assertFalse(self.root.exists())
        self.assertEqual(self.account.read_text(),'existing-login-data')

    def test_command_survives_desktop_removal_and_preserves_arguments_cwd_and_exit(self):
        self.prepare()
        self.package()
        ready=commands.register(self.manager)
        self.assertTrue(ready['ready'])
        self.assertFalse(ready['usingExisting'])
        shutil.rmtree(self.application.parent)
        directory=self.home/'a different project'
        directory.mkdir()
        env=dict(os.environ,PATH='/usr/bin:/bin')
        env.pop('COPILOT_HOME',None)
        result=subprocess.run([str(self.root/'bin/copilot'),'an argument with spaces','$(not-a-command)','--exit-seven'],cwd=directory,
            env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,7,result.stderr)
        value=json.loads(result.stdout)
        self.assertEqual(value['args'],['an argument with spaces','$(not-a-command)','--exit-seven'])
        self.assertEqual(value['cwd'],str(directory))
        self.assertIsNone(value['home'])
        self.assertEqual(self.account.read_text(),'existing-login-data')

    def test_keep_existing_and_prefer_new_reuse_one_owned_shell_block(self):
        self.prepare()
        self.package()
        other=self.home/'other product/bin'
        other.mkdir(parents=True)
        current=other/'copilot'
        current.write_text('#!/bin/sh\necho "copilot 9.0.0"\n')
        current.chmod(0o755)
        original=current.read_bytes()
        profile=self.home/'.bashrc'
        profile.write_text('# my existing configuration\n')
        with patch.dict(os.environ,AIXAND_PROVIDER_USER_PATH=str(other)+':/usr/bin:/bin'):
            self.assertTrue(commands.status(self.manager)['hasExisting'])
            kept=commands.register(self.manager,preference='keep')
            self.assertTrue(kept['ready'])
            self.assertTrue(kept['hasExisting'])
            self.assertEqual(kept['defaultPath'],str(current))
            preferred=commands.register(self.manager,preference='prefer')
            self.assertEqual(preferred['defaultPath'],str(self.root/'bin/copilot'))
            commands.register(self.manager,preference='prefer')
        self.assertEqual(current.read_bytes(),original)
        self.assertEqual(profile.read_text().count('# >>> Aixand provider commands >>>'),1)
        self.assertIn('# my existing configuration',profile.read_text())

    def test_modified_launchers_are_kept_and_prevent_software_removal(self):
        self.prepare()
        self.package()
        commands.register(self.manager)
        target=self.root/'bin/copilot'
        target.write_text('replacement owned by someone else')
        self.assertFalse(commands.status(self.manager)['ready'])
        with self.assertRaisesRegex(Exception,'replaced outside'):
            commands.register(self.manager)
        with self.assertRaisesRegex(Exception,'replaced outside'):
            commands.remove(self.manager)
        self.assertEqual(target.read_text(),'replacement owned by someone else')
        self.assertTrue((self.root/'node').is_dir())

    def test_disable_keeps_public_commands_and_shared_runtime(self):
        self.prepare()
        self.package()
        commands.register(self.manager)
        plan=provider_software.plan(self.manager)
        result=provider_software.perform(self.manager,'disable',revision=plan['revision'],confirmed=True)
        self.assertTrue(result['disabled'])
        self.assertTrue(commands.status(self.manager)['ready'])
        self.assertEqual(self.account.read_text(),'existing-login-data')

    def test_native_providers_do_not_depend_on_an_existing_node_runtime(self):
        self.prepare()
        self.package()
        self.package('claude')
        commands.register(self.manager)
        claude=Manager('claude',config=self.config,root=self.home/'accounts')
        commands.register(claude)
        commands.remove(self.manager)
        self.assertFalse((self.root/'bin/copilot').exists())
        self.assertTrue((self.root/'node').is_dir())
        self.assertTrue(commands.status(claude)['ready'])
        commands.remove_runtime()
        self.assertFalse((self.root/'node').exists())
        self.assertTrue(commands.status(claude)['ready'])
        commands.remove(claude)
        commands.remove_runtime()
        self.assertFalse((self.root/'node').exists())
        self.assertEqual(self.account.read_text(),'existing-login-data')

    def test_redirected_store_and_receipt_traversal_are_rejected(self):
        target=self.home/'someone-elses-directory'
        target.mkdir()
        self.root.symlink_to(target,target_is_directory=True)
        with self.assertRaisesRegex(Exception,'redirected'):
            commands.register(self.manager)
        self.root.unlink()
        self.prepare()
        self.package()
        commands.register(self.manager)
        data=commands.read(self.root)
        data['providers']['copilot']['exports']['../../login.json']='0'*64
        commands.save(self.root,data)
        with self.assertRaisesRegex(Exception,'ownership'):
            commands.remove(self.manager)
        self.assertEqual(self.account.read_text(),'existing-login-data')

    def test_setup_command_never_migrates_an_external_npm_installation(self):
        legacy=self.home/'workspace/tools/node'
        path=self.package(prefix=legacy)
        before=path.read_bytes()
        with patch.dict(os.environ,AIXAND_PROVIDER_LEGACY_PREFIX=str(legacy),PATH=str(legacy/'bin')+':/usr/bin:/bin'),patch.object(install,'run_checked') as runner:
            with self.assertRaisesRegex(Exception,'native provider installation'):
                commands.register(self.manager)
        runner.assert_not_called()
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(self.account.read_text(),'existing-login-data')
        self.assertFalse((self.root/'node').exists())

    def test_legacy_without_ownership_is_not_migrated(self):
        legacy=self.home/'workspace/tools/node'
        path=self.package(prefix=legacy)
        with patch.dict(os.environ,AIXAND_PROVIDER_LEGACY_PREFIX=str(legacy),PATH=str(legacy/'bin')+':/usr/bin:/bin'),patch.object(install,'run_checked') as runner:
            with self.assertRaisesRegex(Exception,'native provider installation'):
                commands.register(self.manager)
        runner.assert_not_called()
        self.assertTrue(path.exists())
        self.assertFalse((self.root/'bin/copilot').exists())


class PathOrderingTests(unittest.TestCase):
    def test_keep_preserves_order_and_prefer_moves_only_the_owned_entry(self):
        self.assertEqual(commands.ordered_path(['/other','/managed','/last'],'/managed','keep'),['/other','/managed','/last'])
        self.assertEqual(commands.ordered_path(['/other','/managed','/last'],'/managed','prefer'),['/managed','/other','/last'])
