"""Typed source/logical selection results and public error boundaries."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import get_type_hints
from unittest.mock import patch

import prompt_manager
from prompt_manager import (
    ApplicationDescriptor,
    ApplicationRenderResult,
    ApplicationResolution,
    DraftTarget,
    ManagedTarget,
    Manager,
    PackagedFallback,
    PackagedTarget,
    PromptError,
    RegistrationIssue,
    ReleaseTarget,
    SourceDescriptor,
    SourceRegistration,
)
from prompt_manager.sources.registry import Registry


class MappingContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.manager = Manager(self.root / "store", packs=[])

    def seed(self):
        self.manager.create_managed("review", content="Review carefully.")
        package = self.manager.package("local", "review")
        snapshot = package.resolve()["snapshot"]
        self.source = self.root / "draft"
        self.source.mkdir()
        (self.source / "pack.json").write_text(json.dumps(snapshot["pack"]))
        for name, text in snapshot["files"].items():
            (self.source / name).write_text(text)
        source = self.manager.register_source("drafts", [self.source], expected_revision=None)
        package.release("1")
        return source

    def assert_contract(self, contract, result):
        self.assertTrue(contract.__required_keys__ <= result.keys(), (contract, result))
        json.dumps(result, allow_nan=False)

    def test_public_types_and_result_annotations_are_resolvable(self):
        for name in (
            "ApplicationDescriptor",
            "ApplicationResolution",
            "ApplicationRenderResult",
            "SourceDescriptor",
            "SourceRegistration",
            "RegistrationIssue",
            "PromptTarget",
            "ManagedTarget",
            "ReleaseTarget",
            "DraftTarget",
            "PackagedFallback",
            "PackagedTarget",
        ):
            self.assertIn(name, prompt_manager.__all__)
            get_type_hints(getattr(prompt_manager, name)) if isinstance(
                getattr(prompt_manager, name), type
            ) else None
        for method in (
            "source_descriptor",
            "register_source",
            "sources",
            "unregister_source",
            "application_descriptor",
            "register_application",
            "applications",
            "set_application_prompt",
            "unregister_application",
            "resolve_application",
            "render_application",
        ):
            self.assertIn("return", get_type_hints(getattr(Manager, method)), method)

    def test_legacy_source_result_and_normalized_lookup_remain_distinct(self):
        legacy = self.manager.register_source("drafts", [self.root / "draft"])
        self.assert_contract(SourceRegistration, legacy)
        self.assertEqual({"schema", "id", "label", "paths"}, set(legacy))
        descriptor = self.manager.source_descriptor("drafts")
        self.assert_contract(SourceDescriptor, descriptor)
        self.assertIsNone(descriptor["owner"])
        self.assertTrue(descriptor["writable"])
        before = (self.manager.store.home / "sources/drafts.json").read_bytes()
        self.assertEqual(descriptor, self.manager.sources()[0])
        self.assertEqual(before, (self.manager.store.home / "sources/drafts.json").read_bytes())
        changed = self.manager.register_source(
            "drafts",
            [self.root / "draft"],
            writable=False,
            owner={"application": "workspace"},
            expected_revision=descriptor["revision"],
        )
        self.assert_contract(SourceDescriptor, changed)
        removed = self.manager.unregister_source("drafts", expected_revision=changed["revision"])
        self.assertEqual(changed, removed)

    def test_absent_descriptor_reads_do_not_create_storage(self):
        for getter in (self.manager.source_descriptor, self.manager.application_descriptor):
            with self.assertRaises(PromptError) as error:
                getter("missing")
            self.assertEqual("not_found", error.exception.code)
        self.assertEqual([], self.manager.sources())
        self.assertEqual([], self.manager.applications())
        self.assertFalse(self.manager.store.home.exists())

    def test_each_target_and_application_result_matches_its_public_contract(self):
        self.seed()
        targets = [
            (
                ManagedTarget,
                {
                    "kind": "managed",
                    "publisher_id": "local",
                    "package_id": "review",
                    "prompt": "review",
                },
            ),
            (ReleaseTarget, {"kind": "release", "reference": "review@1/review"}),
            (
                DraftTarget,
                {
                    "kind": "draft",
                    "source_id": "drafts",
                    "pack_path": str(self.source),
                    "prompt": "review",
                },
            ),
        ]
        for index, (contract, target) in enumerate(targets):
            with self.subTest(kind=target["kind"]):
                self.assert_contract(contract, target)
                application = self.manager.register_application(
                    "workspace" + str(index), prompts={"review": target}
                )
                self.assert_contract(ApplicationDescriptor, application)
                resolved = self.manager.resolve_application(application["id"], "review")
                self.assert_contract(ApplicationResolution, resolved)
                self.assertEqual(target, resolved["target"])
                if target["kind"] == "managed":
                    self.assertIn("managed", resolved)
                else:
                    self.assertNotIn("managed", resolved)
                rendered = self.manager.render_application(
                    application["id"], "review", format="messages"
                )
                self.assert_contract(ApplicationRenderResult, rendered)
                self.assertEqual(
                    [{"role": "user", "content": "Review carefully."}], rendered["output"]
                )
                self.assertEqual(resolved, rendered["resolution"])
                self.assertEqual(
                    application, self.manager.application_descriptor(application["id"])
                )
        for application in self.manager.applications():
            self.assert_contract(ApplicationDescriptor, application)

    def test_packaged_fallback_is_typed_and_does_not_hide_configured_failures(self):
        self.seed()
        fallback = {"pack_path": str(self.source), "prompt": "review"}
        self.assert_contract(PackagedFallback, fallback)
        resolved = self.manager.resolve_application("absent", "review", fallback=fallback)
        self.assert_contract(ApplicationResolution, resolved)
        self.assert_contract(PackagedTarget, resolved["target"])
        self.assertTrue(resolved["used_fallback"])
        self.assertIsNone(resolved["application_revision"])
        self.assertIsNone(resolved["source_id"])
        self.assertIsNone(resolved["catalog_entry_id"])
        self.assertNotIn("managed", resolved)
        self.manager.register_application(
            "configured",
            fallback_policy="packaged",
            prompts={
                "review": {"kind": "release", "reference": "missing@1/review"},
            },
        )
        with self.assertRaises(PromptError) as error:
            self.manager.render_application("configured", "review", fallback=fallback)
        self.assertEqual("not_found", error.exception.code)

    def test_guarded_mapping_mutations_preserve_descriptor_shape_and_conflict_codes(self):
        self.seed()
        app = self.manager.register_application(
            "workspace",
            prompts={
                "review": {"kind": "release", "reference": "review@1/review"},
            },
        )
        changed = self.manager.set_application_prompt(
            "workspace", "review", None, expected_revision=app["revision"]
        )
        self.assert_contract(ApplicationDescriptor, changed)
        self.assertEqual({}, changed["prompts"])
        with self.assertRaises(PromptError) as error:
            self.manager.unregister_application("workspace", expected_revision=app["revision"])
        self.assertEqual("conflict", error.exception.code)
        self.assertEqual(
            changed,
            self.manager.unregister_application("workspace", expected_revision=changed["revision"]),
        )
        self.assertIn("Review carefully.", self.manager.render("review@1/review")["output"])

    def test_broken_list_entries_keep_error_strings_and_add_structured_diagnostics(self):
        self.seed()
        self.manager.register_application("healthy", prompts={})
        for kind, listing in (
            ("sources", self.manager.sources),
            ("applications", self.manager.applications),
        ):
            with self.subTest(kind=kind):
                (self.manager.store.home / kind / "broken.json").write_text("{")
                rows = listing()
                issue = next(row for row in rows if row["id"] == "broken")
                self.assert_contract(RegistrationIssue, issue)
                self.assertIsInstance(issue["error"], str)
                self.assertEqual("json", issue["diagnostic"]["code"])
                self.assertEqual(issue["error"], issue["diagnostic"]["message"])
                self.assertEqual(2, len(rows))
        original = Registry.get

        def permission_error(registry, kind, identifier, **options):
            if identifier == "broken":
                raise PermissionError("Denied")
            return original(registry, kind, identifier, **options)

        with patch.object(Registry, "get", side_effect=permission_error, autospec=True):
            issue = next(row for row in self.manager.sources() if row["id"] == "broken")
        self.assertEqual("io", issue["diagnostic"]["code"])

    def test_public_filesystem_errors_are_normalized_but_domain_and_programmer_errors_survive(self):
        cases = [
            ("source", lambda: self.manager.register_source("drafts", [self.root])),
            ("get", lambda: self.manager.source_descriptor("drafts")),
            ("list", self.manager.sources),
            ("remove", lambda: self.manager.unregister("sources", "drafts")),
            (
                "remove_guarded",
                lambda: self.manager.unregister_source("drafts", expected_revision="observed"),
            ),
            ("application", lambda: self.manager.register_application("workspace", prompts={})),
            ("get", lambda: self.manager.application_descriptor("workspace")),
            ("list", self.manager.applications),
            (
                "set_prompt",
                lambda: self.manager.set_application_prompt(
                    "workspace", "review", None, expected_revision="observed"
                ),
            ),
            (
                "remove_guarded",
                lambda: self.manager.unregister_application(
                    "workspace", expected_revision="observed"
                ),
            ),
        ]
        for method, call in cases:
            with (
                self.subTest(method=method),
                patch.object(Registry, method, side_effect=PermissionError("Denied")),
            ):
                with self.assertRaises(PromptError) as error:
                    call()
                self.assertEqual("io", error.exception.code)
        for dependency, call in (
            ("capture", lambda: self.manager.resolve_application("workspace", "review")),
            ("render_application", lambda: self.manager.render_application("workspace", "review")),
        ):
            with patch(
                "prompt_manager.applications.resolution." + dependency,
                side_effect=PermissionError("Denied"),
            ):
                with self.assertRaises(PromptError) as error:
                    call()
                self.assertEqual("io", error.exception.code)
        conflict = PromptError("conflict", "Changed")
        with patch.object(Registry, "source", side_effect=conflict):
            with self.assertRaises(PromptError) as error:
                self.manager.register_source("drafts", [self.root])
            self.assertIs(conflict, error.exception)
        with patch.object(Registry, "source", side_effect=TypeError("Programmer error")):
            with self.assertRaises(TypeError):
                self.manager.register_source("drafts", [self.root])

    def test_source_paths_reject_nul_with_a_validation_error(self):
        with self.assertRaises(PromptError) as error:
            self.manager.register_source("bad", ["/bad\0path"])
        self.assertEqual("validation", error.exception.code)
        self.assertFalse(self.manager.store.home.exists())

    def test_cli_descriptor_output_and_io_error_envelope_remain_compatible(self):
        descriptor = self.manager.register_application("workspace", prompts={})
        shown = subprocess.run(
            [
                sys.executable,
                "-m",
                "prompt_manager",
                "--home",
                str(self.manager.store.home),
                "application",
                "show",
                "workspace",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, shown.returncode, shown.stderr)
        self.assertEqual(descriptor, json.loads(shown.stdout))
        blocked = self.root / "blocked"
        blocked.write_text("This is a file, not a store directory")
        failed = subprocess.run(
            [
                sys.executable,
                "-m",
                "prompt_manager",
                "--home",
                str(blocked),
                "application",
                "register",
                "workspace",
                "--prompts",
                "{}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(1, failed.returncode)
        self.assertEqual("io", json.loads(failed.stderr)["error"]["code"])
