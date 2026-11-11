"""Native delivery contracts without importing any resource/Agent package."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from conversation_manager import ConversationError
from conversation_manager.models import digest
from conversation_manager.skill_delivery import (
    input_items,
    require_native_catalog,
    verify_export,
)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="native-skill-delivery-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name) / "audit"
        root.mkdir()
        (root / "empty").mkdir()
        (root / "SKILL.md").write_bytes(
            b"---\nname: audit\ndescription: Fixture audit\n---\nFixture rules\n"
        )
        (root / ".binary").write_bytes(b"\x00\xff")
        files = [
            {
                "path": p.name,
                "size": len(p.read_bytes()),
                "digest": "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest(),
                "executable": False,
            }
            for p in sorted(root.iterdir())
            if p.is_file()
        ]
        manifest = {
            "schema": "skill-manager/package-1",
            "name": "audit",
            "metadata": {"name": "audit", "description": "Fixture audit"},
            "files": files,
            "directories": ["empty"],
        }
        self.row = {
            "name": "audit",
            "export": str(root),
            "manifest": manifest,
            "digest": digest(manifest),
        }

    async def test_complete_manifest_and_native_input_preserve_external_identity(self):
        verify_export(self.row)
        self.assertEqual(
            input_items([self.row]),
            [
                {
                    "type": "skill",
                    "name": "audit",
                    "path": self.row["export"] + "/SKILL.md",
                }
            ],
        )

    async def test_command_turns_keep_the_sessions_captured_skill_inputs(self):
        from conversation_manager.adapters.codex import CodexAdapter
        from conversation_manager.native_controls import NativeControls
        adapter = CodexAdapter.__new__(CodexAdapter)
        adapter.turns = {}
        adapter.outputs = {}
        adapter.core = SimpleNamespace(store=SimpleNamespace(get=lambda *_: {'settings': {'skills': [self.row]}}))
        async def rpc(method, params):
            self.assertEqual(method, 'turn/start')
            self.assertIn({'type': 'skill', 'name': 'audit', 'path': self.row['export'] + '/SKILL.md'}, params['input'])
            adapter.turns['thread'].set_result({'id': 'native-turn', 'status': 'completed'})
            return {'turn': {'id': 'native-turn'}}
        adapter.controls = NativeControls(rpc)
        result = await adapter.command_prompt({'session_id': 'session', 'native_session_id': 'thread'}, {'name': 'init'}, {'native_turn_id': None})
        self.assertEqual(result['stop_reason'], 'end_turn')

    async def test_changed_bytes_and_symlinks_are_integrity_errors(self):
        root = Path(self.row["export"])
        (root / ".binary").write_bytes(b"changed")
        with self.assertRaises(ConversationError) as caught:
            verify_export(self.row)
        self.assertEqual(caught.exception.code, "integrity")
        (root / ".binary").unlink()
        (root / ".binary").symlink_to(root / "SKILL.md")
        with self.assertRaises(ConversationError):
            verify_export(self.row)

    async def test_catalog_missing_or_disabled_required_path_is_unsupported(self):
        async def rpc(method, params):
            self.assertEqual(method, "skills/list")
            self.assertTrue(params["forceReload"])
            return {
                "data": [
                    {
                        "cwd": self.temp.name,
                        "skills": [
                            {"path": self.row["export"] + "/SKILL.md", "enabled": False}
                        ],
                    }
                ]
            }

        with self.assertRaises(ConversationError) as caught:
            await require_native_catalog(rpc, self.temp.name, [self.row])
        self.assertEqual(caught.exception.code, "unsupported")

    async def test_catalog_accepts_only_the_exact_enabled_export(self):
        async def rpc(method, params):
            return {
                "data": [
                    {
                        "cwd": self.temp.name,
                        "skills": [
                            {"path": self.row["export"] + "/SKILL.md", "enabled": True}
                        ],
                    }
                ]
            }

        await require_native_catalog(rpc, self.temp.name, [self.row])


if __name__ == "__main__":
    unittest.main()
