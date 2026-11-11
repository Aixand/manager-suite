"""Declarative service discovery, account boundaries and editable catalog caching."""
from dataclasses import replace
import copy
import json
import os
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from agent_manager import Error
from agent_manager.providers.config import load_registry
from agent_manager.services.loader import DEFINITIONS, load_services
from agent_manager.services.schema import ServiceError, parse
from tests.support import native_manifest
from tests.test_integrations import IntegrationFixture


def service_manifest(identifier='example', native_id='native-example'):
    return f'''schema_version = 1
id = "{identifier}"
label = "Example service"
[discovery.opencode]
integration_ids = ["{native_id}"]
packages = ["@example/runtime"]
endpoints = ["https://example.test/v1"]
[authentication.key]
budget_kind = "api-billing"
'''


class ServiceManifestTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.config = self.base / 'providers.toml'; self.config.write_text('')
        self.services = self.base / 'services'; self.services.mkdir()

    def test_provider_service_identity_is_distinct_from_cli_identity(self):
        registry = load_registry(self.config)
        for cli, service in (('codex', 'openai'), ('claude', 'anthropic'),
                ('copilot', 'github'), ('antigravity', 'google'), ('opencode', None)):
            self.assertEqual(registry.resolve(cli).capabilities()['service'], service)
        rules = {row.id: row for row in registry.resolve('opencode').integrations.rules}
        self.assertEqual(rules['openai'].cli_providers, ('codex',))
        self.assertFalse(rules['openrouter'].cli_providers)
        self.assertEqual(rules['openai'].authentication_for('oauth').budget_kind, 'subscription')
        self.assertEqual(rules['openai'].authentication_for('key').budget_kind, 'api-billing')

    def test_new_service_and_cli_are_matched_without_registration_code(self):
        (self.services / 'example.toml').write_text(service_manifest())
        providers = self.base / 'providers'; providers.mkdir()
        document = native_manifest('example-cli').replace('id = "example-cli"',
            'id = "example-cli"\nservice = "example"')
        (providers / 'example.toml').write_text(document)
        rules = load_registry(self.config).resolve('opencode').integrations.rules
        rule = next(row for row in rules if row.id == 'native-example')
        self.assertEqual(rule.service_id, 'example')
        self.assertEqual(rule.cli_providers, ('example-cli',))
        self.assertEqual(rule.authentication_for('key').budget_adapter, 'disabled')

    def test_ambiguous_native_ids_and_duplicate_service_ids_are_rejected(self):
        (self.services / 'one.toml').write_text(service_manifest())
        (self.services / 'two.toml').write_text(service_manifest())
        with self.assertRaisesRegex(ServiceError, 'duplicate service ID'): load_services(config=self.config)
        (self.services / 'two.toml').write_text(service_manifest('different'))
        with self.assertRaisesRegex(ServiceError, 'ambiguous native integration ID'): load_services(config=self.config)

    def test_local_overrides_replace_whole_service_definitions(self):
        (self.services / 'openai.toml').write_text('schema_version=1\nid="openai"\nlabel="Disabled discovery"\n')
        registry = load_registry(self.config)
        self.assertEqual(registry.resolve('codex').service, 'openai')
        self.assertEqual([row.id for row in registry.resolve('opencode').integrations.rules], ['openrouter'])

    def test_unknown_service_references_are_visible_configuration_errors(self):
        providers = self.base / 'providers'; providers.mkdir()
        document = native_manifest().replace('id = "test"', 'id = "test"\nservice = "missing"')
        path = providers / 'test.toml'; path.write_text(document)
        with self.assertRaisesRegex(Error, 'unknown service reference'): load_registry(self.config)
        path.unlink()
        document = (Path('agent_manager/providers/definitions/opencode.toml').read_text()
            .replace('adapter = "opencode-integrations"', 'adapter = "opencode-integrations"\nservices = ["missing"]'))
        (providers / 'opencode.toml').write_text(document)
        with self.assertRaisesRegex(Error, 'unknown integration service reference'): load_registry(self.config)

    def test_invalid_manifests_hide_values_and_cannot_supply_code_or_credentials(self):
        original = tomllib.loads((DEFINITIONS / 'openai.toml').read_text())
        for transform in (lambda row: row.update(schema_version=True),
                lambda row: row.update(token='fixture-secret'),
                lambda row: row['authentication']['key'].update(command='fixture-secret'),
                lambda row: row['authentication']['key'].update(budget_adapter='python:fixture-secret'),
                lambda row: row['authentication']['key'].update(budget_kind=[]),
                lambda row: row['discovery']['opencode'].update(integration_ids=['openai', 'openai']),
                lambda row: row['discovery']['opencode'].update(packages=[]),
                lambda row: row['discovery']['opencode'].update(endpoints=['https://fixture-secret@example.test/v1']),
                lambda row: row['discovery']['opencode'].update(endpoints=['https://example.test/v1?token=fixture-secret'])):
            value = copy.deepcopy(original); transform(value)
            with self.assertRaises(ServiceError) as caught: parse(value)
            self.assertNotIn('fixture-secret', str(caught.exception))
        (self.services / 'broken.toml').write_text('id = "fixture-secret\n')
        with self.assertRaisesRegex(Error, 'invalid TOML service manifest') as caught: load_registry(self.config)
        self.assertNotIn('fixture-secret', str(caught.exception))

    def test_readers_cannot_be_repurposed_for_another_service_or_authentication_type(self):
        original = tomllib.loads((DEFINITIONS / 'openrouter.toml').read_text())
        for transform in (lambda row: row.update(id='other'),
                lambda row: row['authentication'].update(oauth=row['authentication'].pop('key')),
                lambda row: row['authentication']['key'].update(budget_kind='subscription')):
            value = copy.deepcopy(original); transform(value)
            with self.assertRaisesRegex(ServiceError, 'does not support'): parse(value)


@unittest.skipIf(os.name == 'nt', 'POSIX disposable executable fixture')
class ServiceDiscoveryTests(IntegrationFixture, unittest.TestCase):
    def test_matching_a_service_never_reads_a_related_cli_account(self):
        with patch.object(self.managers['codex'], 'budget', side_effect=AssertionError('unverified account reuse')):
            result = self.manager.budget(cwd=self.base, model='openai/native')
        self.assertEqual(result['selected_route']['service_id'], 'openai')
        self.assertEqual(result['selected_route']['cli_providers'], ['codex'])
        self.assertEqual(result['selected_route']['budget_kind'], 'subscription')
        self.assertEqual(result['selected_route']['connection_id'], 'cred_openai')
        self.assertFalse(result['buckets'])
        self.assertEqual(result['status'], 'unknown')

    def test_model_creator_does_not_override_the_native_paying_service(self):
        result = self.manager.budget(cwd=self.base, model='openrouter/openai/native')
        self.assertEqual(result['selected_route']['service_id'], 'openrouter')
        self.assertEqual(result['selected_route']['budget_kind'], 'credits')
        self.assertEqual(result['selected_route']['cli_providers'], [])

    def test_new_declarative_service_is_discovered_with_unknown_budget(self):
        services = self.base / 'services'; services.mkdir()
        (services / 'example.toml').write_text(service_manifest())
        self.native['data'].append(dict(id='native-example', name='Example', methods=[dict(type='key')],
            connections=[dict(type='credential', method='key', id='example-account')]))
        self.bridge['providers'].append(dict(id='example-route', integration_id='native-example',
            package='@example/runtime', endpoint_reviewed=True))
        self.bridge['integrations'].append(dict(id='native-example',
            active_connection=dict(type='credential', method='key', id='example-account'),
            budget=dict(status='unknown', reason='reader-unavailable')))
        result = self.manager.integrations(cwd=self.base)
        row = next(row for row in result['integrations'] if row['id'] == 'native-example')
        self.assertTrue(row['recognized']); self.assertTrue(row['reviewed'])
        self.assertEqual(row['service_id'], 'example')
        self.assertEqual(row['connection_status'], 'selected')
        self.assertEqual(row['budget']['reason'], 'reader-unavailable')
        self.assertEqual(row['budget_kind'], 'api-billing')
        self.assertIn('native-example', [row.id for row in self.probe.call_args.args[3].rules])

    def test_live_catalog_edits_invalidate_cache_without_waiting_five_minutes(self):
        self.manager.integrations(cwd=self.base)
        services = self.base / 'services'; services.mkdir()
        source = (DEFINITIONS / 'openai.toml').read_text().replace('budget_adapter = "openai-native"',
            'budget_adapter = "disabled"')
        (services / 'openai.toml').write_text(source)
        cached = self.manager.integrations(cwd=self.base, cached=True)
        self.assertEqual(cached['status'], 'unknown')
        self.assertFalse(cached['integrations'])
        result = self.manager.integrations(cwd=self.base)
        self.assertEqual(self.probe.call_count, 2)
        self.assertEqual(result['integrations'][0]['budget']['adapter'], 'disabled')
        self.assertEqual(result['integrations'][0]['budget']['reason'], 'reader-unavailable')

    def test_service_filter_limits_native_queries_and_keeps_unknown_inventory_visible(self):
        policy = replace(self.manager.provider_spec.integrations, service_ids=('openai',))
        self.manager.provider_spec = replace(self.manager.provider_spec, integrations=policy)
        result = self.manager.integrations(cwd=self.base)
        self.assertEqual([row.id for row in self.probe.call_args.args[3].rules], ['openai'])
        row = next(row for row in result['integrations'] if row['id'] == 'openrouter')
        self.assertFalse(row['recognized'])
        self.assertIsNone(row['service_id'])
        self.assertFalse(row['budget']['buckets'])

    def test_recognition_selection_and_allowance_are_independent_states(self):
        self.bridge['integrations'][0]['active_connection'] = None
        self.bridge['integrations'][0]['budget'] = dict(status='unknown', reason='no-active-connection')
        result = self.manager.integrations(cwd=self.base)
        row = result['integrations'][0]
        self.assertTrue(row['recognized'])
        self.assertEqual(row['cli_providers'], ['codex'])
        self.assertEqual(row['connection_status'], 'none')
        self.assertEqual(row['budget_kind'], 'unknown')
        self.assertEqual(row['budget']['reason'], 'no-active-connection')

    def test_unreviewed_routes_never_claim_a_matched_paying_service(self):
        self.bridge['providers'][0]['package'] = 'custom-package'
        result = self.manager.budget(cwd=self.base, model='openai/native')
        self.assertIsNone(result['selected_route']['service_id'])
        self.assertFalse(result['buckets'])

    def test_old_cache_contract_is_rejected(self):
        self.manager.integrations(cwd=self.base)
        path = self.path(); value = json.loads(path.read_text())
        value['schema'] = 'agent-manager/integrations-1'; path.write_text(json.dumps(value))
        self.assertFalse(self.manager.integrations(cwd=self.base, cached=True)['integrations'])
