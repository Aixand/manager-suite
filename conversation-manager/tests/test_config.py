"""Editable defaults, deterministic overrides, and retained-owner isolation."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from conversation_manager import Client, ConversationError
from conversation_manager.config import initialize, load, path, process_defaults
from conversation_manager.store import Store


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"HOME": str(self.root)}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def write(self, content):
        config = self.root / "custom" / "config.toml"
        config.parent.mkdir(exist_ok=True)
        config.write_text(content)
        return config

    def test_new_layout_has_no_side_effects_and_does_not_adopt_old_state(self):
        old = self.root / ".local/share/conversation-manager"
        old.mkdir(parents=True)
        (old / "legacy.txt").write_text("unchanged")
        client = Client()
        expected = self.root / ".local/share/aixand/manager/conversation"
        self.assertEqual(path(), expected / "config.toml")
        self.assertEqual(client.home, expected / "state")
        self.assertEqual(Store().home, client.home)
        self.assertFalse(expected.exists())
        self.assertEqual(Client(old).home, old)
        self.assertEqual((old / "legacy.txt").read_text(), "unchanged")

    def test_init_creates_editable_private_file_without_overwriting(self):
        first = initialize()
        config = Path(first["path"])
        self.assertTrue(first["created"])
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        config.write_text(
            '[runtime]\nkind="standalone"\n[process.labels]\nproject="mine"\n'
        )
        self.assertFalse(initialize()["created"])
        self.assertEqual(
            Client().configuration["process"], {"labels": {"project": "mine"}}
        )
        self.assertFalse((config.parent / "state").exists())

    def test_config_selection_and_storage_precedence(self):
        selected = self.write('[storage]\nhome="records"\n')
        self.assertEqual(Client(config=selected).home, selected.parent / "records")
        with patch.dict(
            os.environ,
            {
                "CONVERSATION_MANAGER_CONFIG": str(selected),
                "CONVERSATION_MANAGER_HOME": str(self.root / "env-state"),
            },
        ):
            self.assertEqual(Client().home, self.root / "env-state")
            self.assertEqual(Client(self.root / "sdk").home, self.root / "sdk")
            second = self.root / "second.toml"
            second.write_text("")
            self.assertEqual(Client(config=second).configuration["path"], str(second))

    def test_runtime_precedence_dormant_daemon_options_and_config_relative_paths(self):
        selected = self.write(
            '[runtime]\nkind="undercurrent"\n[runtime.undercurrent]\nhome="daemon"\nbridge="bin/bridge"\n'
        )
        self.assertEqual(
            Client(config=selected, runtime="standalone").runtime,
            {"kind": "standalone", "options": {}},
        )
        with patch.dict(os.environ, {"CONVERSATION_MANAGER_RUNTIME": "standalone"}):
            self.assertEqual(Client(config=selected).runtime["kind"], "standalone")
        from conversation_manager.config import runtime_defaults

        settings = load(selected)
        self.assertEqual(
            settings["runtime_options"]["home"], str(selected.parent / "daemon")
        )
        with patch.dict(
            os.environ, {"UNDERCURRENT_HOME": str(self.root / "env-daemon")}
        ):
            kind, options = runtime_defaults(settings)
            self.assertEqual(kind, "undercurrent")
            self.assertEqual(options["home"], str(self.root / "env-daemon"))
            self.assertEqual(
                runtime_defaults(
                    settings, options={"home": str(self.root / "sdk-daemon")}
                )[1]["home"],
                str(self.root / "sdk-daemon"),
            )

    def test_process_defaults_merge_with_per_launch_overrides_and_stable_names(self):
        selected = self.write(
            '[process]\nname_prefix="project"\n[process.labels]\napp="chat"\nproject="original"\n[process.metadata]\npurpose="development"\n'
        )
        client = Client(config=selected)
        first = process_defaults(
            client, {"labels": {"project": "override"}}, "operation"
        )
        self.assertTrue(first["name"].startswith("project-"))
        self.assertEqual(first["labels"], {"app": "chat", "project": "override"})
        self.assertEqual(first["metadata"], {"purpose": "development"})
        self.assertEqual(
            first,
            process_defaults(client, {"labels": {"project": "override"}}, "operation"),
        )
        self.assertNotEqual(
            first["name"], process_defaults(client, None, "other")["name"]
        )
        self.assertEqual(
            process_defaults(client, {"name": "Explicit"}, "operation")["name"],
            "Explicit",
        )
        self.assertEqual(
            client.configuration["process"]["labels"]["project"], "original"
        )

    def test_explicit_missing_or_invalid_config_never_silently_falls_back(self):
        with self.assertRaises(ConversationError):
            Client(config=self.root / "missing")
        for content in (
            "[runtime",
            "[unexpected]\nvalue=1",
            '[runtime]\nkind="other"',
            '[process.metadata]\napi_key="private"',
            "[runtime.undercurrent]\nrequest_timeout=nan",
            '[process]\nname_prefix=""',
            "[storage]\nhome=12",
        ):
            with self.subTest(content=content), self.assertRaises(ConversationError):
                Client(config=self.write(content))
        self.assertFalse((self.root / ".aixand").exists())

    def test_cli_init_show_and_explicit_runtime_override(self):
        config = self.root / "cli/config.toml"

        def run(*args):
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-m",
                    "conversation_manager",
                    "--config",
                    str(config),
                    *args,
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            return json.loads(result.stdout)

        self.assertTrue(run("config", "init", "--json")["created"])
        self.assertEqual(run("config", "path")["path"], str(config))
        self.assertEqual(run("config", "show")["runtime"], "standalone")
        config.write_text(
            '[runtime]\nkind="undercurrent"\n[runtime.undercurrent]\nbridge="/does-not-exist"\n'
        )
        self.assertEqual(run("config", "show")["runtime"], "undercurrent")
        self.assertEqual(run("--runtime", "standalone", "conversation", "list"), [])
