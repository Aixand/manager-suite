"""Provider inventory separates profile configuration, installation and login status."""
import uuid
import unittest
from unittest.mock import patch

from agent_manager import Error, Manager
from agent_manager.profiles.operations import Management
from agent_manager.providers.catalog import catalog, profile_readiness
from tests.support import ProfileFixture


class ProviderCatalogTests(ProfileFixture, unittest.TestCase):
    def test_discovery_includes_custom_manifest_without_initializing_profile_stores(self):
        def installation(manager):
            return dict(provider=manager.provider, installed=False, usable=False, terminalUsable=False, version=None, methods=[])
        with patch('agent_manager.providers.catalog.inspect_installation', side_effect=installation), \
                patch('agent_manager.providers.catalog.install.environment_info', return_value={}):
            result = catalog(config=self.config, home=self.base / 'homes')
        custom = next(row for row in result['providers'] if row['provider'] == 'test')
        self.assertEqual(result['schema'], 'agent-manager/provider-catalog-1')
        self.assertFalse(result['configured']); self.assertTrue(result['inventoryComplete'])
        self.assertEqual(custom['capabilities']['loginMethods'], ['terminal'])
        self.assertFalse(self.manager.state_path.exists())

    def test_configuration_failure_does_not_become_an_empty_inventory(self):
        self.config.write_text('[providers.unknown]\nexecutable="missing"\n')
        with self.assertRaisesRegex(Error, 'requires a manifest'):
            catalog(config=self.config, home=self.base / 'homes')

    def test_provider_inspection_failure_remains_visible(self):
        def installation(manager):
            if manager.provider == 'test': raise OSError('fixture inventory failure')
            return dict(provider=manager.provider, installed=False, usable=False, terminalUsable=False)
        with patch('agent_manager.providers.catalog.inspect_installation', side_effect=installation), \
                patch('agent_manager.providers.catalog.install.environment_info', return_value={}):
            result = catalog(config=self.config, home=self.base / 'homes')
        self.assertFalse(result['inventoryComplete'])
        custom = next(row for row in result['providers'] if row['provider'] == 'test')
        self.assertFalse(custom['available']); self.assertIsNone(custom['configured'])

    def test_creation_receipt_and_installation_readiness_do_not_claim_sign_in(self):
        manager = self.manager; operation = Management(manager); request = str(uuid.uuid4())
        first = operation.execute('create_profile', {'name': 'work'}, request_id=request)
        self.assertEqual(operation.execute('create_profile', {'name': 'work'}, request_id=request), first)
        ready = profile_readiness(manager, 'work', installation={'terminalUsable': True})
        self.assertTrue(ready['ready']); self.assertEqual(ready['terminal']['state'], 'unknown')
        self.assertEqual(ready['terminal']['verification'], 'unchecked')
        self.assertFalse(profile_readiness(manager, 'work', installation={'terminalUsable': False})['ready'])
