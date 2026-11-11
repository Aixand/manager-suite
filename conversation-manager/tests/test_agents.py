import sys
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from conversation_manager import AgentEnvironments, ConversationError


class AgentEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.target = dict(schema='agent-manager/target-1', provider='codex',
            registry='/registry/codex.json', environment_id='immutable-id', home='/native/home',
            ownership='managed', aliases=['main'], capabilities={})
        self.manager = Mock(provider='codex')
        self.manager.agent_targets.return_value = [self.target]
        self.manager.resolve_agent_target.return_value = self.target

    def test_uses_public_api_and_preserves_identity(self):
        constructor = Mock(return_value=self.manager)
        with patch.dict(sys.modules, {'agent_manager': SimpleNamespace(Manager=constructor)}):
            environments = AgentEnvironments('codex', root='/registry')
        constructor.assert_called_once_with('codex', config=None, root=str(Path('/registry').resolve()))
        targets = environments.list()
        targets[0]['aliases'].append('mutated')
        self.assertEqual(environments.resolve('immutable-id'), self.target)
        self.manager.resolve_agent_target.assert_called_once_with('immutable-id')
        self.assertEqual(self.target['aliases'], ['main'])

    @unittest.skipIf(os.name == 'nt', 'POSIX directory alias')
    def test_source_aliases_use_the_same_identity_in_parent_and_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            actual = root / 'actual'
            actual.mkdir()
            alias = root / 'alias'
            alias.symlink_to(actual, target_is_directory=True)
            constructor = Mock(return_value=self.manager)
            with patch.dict(sys.modules, {'agent_manager': SimpleNamespace(Manager=constructor)}):
                environments = AgentEnvironments(root=alias / 'agents', config=alias / 'providers.toml')
            constructor.assert_called_once_with('codex', root=environments.identity['root'],
                                                config=environments.identity['config'])
            self.assertEqual(environments.identity['root'], str((actual / 'agents').resolve()))

    def test_retired_environment_error_propagates_without_alias_fallback(self):
        self.manager.resolve_agent_target.side_effect = ValueError('retired')
        with patch.dict(sys.modules, {'agent_manager': SimpleNamespace(Manager=Mock(return_value=self.manager))}):
            environments = AgentEnvironments()
        with self.assertRaisesRegex(ValueError, 'retired'):
            environments.resolve('retired-id')
        self.manager.agent_targets.assert_not_called()

    def test_incompatible_target_is_rejected(self):
        self.manager.resolve_agent_target.return_value = {**self.target, 'provider': 'claude'}
        with patch.dict(sys.modules, {'agent_manager': SimpleNamespace(Manager=Mock(return_value=self.manager))}):
            environments = AgentEnvironments()
        with self.assertRaises(ConversationError):
            environments.resolve('immutable-id')

    def test_missing_optional_dependency_has_actionable_error(self):
        with patch.dict(sys.modules, {'agent_manager': None}):
            with self.assertRaisesRegex(ConversationError, r'\[agents\]'):
                AgentEnvironments()
