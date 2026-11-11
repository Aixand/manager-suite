"""Exercise selective checks, independent release versions, and archive boundaries."""

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import tarfile
import tempfile
import tomllib
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import build_installers
import ci_plan
import ci_release
import ci_product
import release_identity
import pypi_release
import product_payload
import prepare_opencode_fixture as opencode_fixture
import qualify_product_installation
import qualify_release_archive


class WindowsArchiveToolTests(unittest.TestCase):
    def test_uses_system_tar_instead_of_git_tar_from_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            native = root / "System32" / "tar.exe"
            native.parent.mkdir()
            native.write_bytes(b"native")
            self.assertEqual(
                qualify_release_archive.windows_archive_tool(
                    {
                        "SystemRoot": str(root),
                        "PATH": str(root / "git"),
                    }
                ),
                native,
            )
            native.unlink()
            with self.assertRaisesRegex(RuntimeError, "System32"):
                qualify_release_archive.windows_archive_tool({"SystemRoot": str(root)})


class PyPIReleaseTests(unittest.TestCase):
    def test_retries_only_select_missing_files_and_reject_changed_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            wheel = Path(temporary) / "package.whl"
            source = Path(temporary) / "package.tar.gz"
            wheel.write_bytes(b"qualified wheel")
            source.write_bytes(b"qualified source")
            files = [wheel, source]
            published = [
                {
                    "filename": wheel.name,
                    "digests": {
                        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()
                    },
                }
            ]
            self.assertEqual(pypi_release.select_missing(files, []), files)
            self.assertEqual(pypi_release.select_missing(files, published), [source])
            published.append(
                {
                    "filename": source.name,
                    "digests": {
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest()
                    },
                }
            )
            self.assertEqual(pypi_release.select_missing(files, published), [])
            wheel.write_bytes(b"changed wheel")
            with self.assertRaisesRegex(ValueError, "differs"):
                pypi_release.select_missing(files, published)


class ProductScopeTests(unittest.TestCase):
    def test_collection_builds_only_the_five_local_managers(self):
        import manager_profiles
        import suite

        expected = {
            "agent-manager",
            "conversation-manager",
            "mcp-manager",
            "prompt-manager",
            "skill-manager",
        }
        manifest = tomllib.loads((ROOT / "installation.toml").read_text())
        self.assertEqual(set(ci_plan.PRODUCTS), expected)
        self.assertEqual(set(suite.COMPONENTS), expected)
        self.assertEqual(set(manager_profiles.PROFILES), expected)
        selections = {"manager-suite/" + name for name in expected}
        self.assertEqual(set(manifest["products"]), selections)
        self.assertEqual(
            set(manifest["groups"]["manager-suite"]["products"]), selections
        )
        self.assertFalse(manifest.get("sources"))
        for component in manifest["components"].values():
            self.assertNotIn("source", component)

    def test_collection_has_no_external_product_checkout(self):
        for product in (*ci_plan.PRODUCTS, "manager-suite"):
            values = ci_release.source_outputs(product, release=True, installer=True)
            self.assertTrue(
                all(
                    value == "false"
                    for key, value in values.items()
                    if key.endswith("-needed")
                )
            )


class ArchiveLauncherRegressionTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "Fixture uses a POSIX command shim")
    def test_broken_installed_command_cannot_pass_through_bootstrap_exec(self):
        from qualify_release_archive import qualify

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "fixture"
            (root / "bootstrap").mkdir(parents=True)
            (root / "product-descriptor.json").write_text(
                json.dumps(
                    {
                        "selection": "fixture",
                        "target": "fixture",
                        "products": {"fixture": {"commands": ["broken"]}},
                    }
                )
            )
            (root / "bootstrap/bootstrap.sh").write_text(
                "#!/bin/sh\n"
                'if [ "$1" = install ]; then\n'
                '  while [ "$1" != --prefix ]; do shift; done\n'
                "  shift\n"
                '  mkdir -p "$1/bin"\n'
                '  printf "#!/bin/sh\\nexit 17\\n" > "$1/bin/broken"\n'
                '  /bin/chmod +x "$1/bin/broken"\n'
                "fi\n"
                "exit 0\n"
            )
            archive = base / "fixture.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                bundle.add(root, arcname="fixture")
            with self.assertRaisesRegex(RuntimeError, "bin/broken"):
                qualify(archive)


class WindowsLauncherImportTests(unittest.TestCase):
    def image(self):
        image = bytearray(1024)
        image[:2] = b"MZ"
        struct.pack_into("<I", image, 0x3C, 0x80)
        image[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<H", image, 0x86, 1)
        struct.pack_into("<H", image, 0x94, 0xF0)
        struct.pack_into("<H", image, 0x98, 0x20B)
        struct.pack_into("<I", image, 0x98 + 120, 0x1000)
        struct.pack_into("<IIII", image, 0x188 + 8, 0x200, 0x1000, 0x200, 0x200)
        struct.pack_into("<IIIII", image, 0x200, 0, 0, 0, 0x1040, 0)
        struct.pack_into("<IIIII", image, 0x214, 0, 0, 0, 0x1060, 0)
        for offset, name in (
            (0x240, b"VCRUNTIME140.dll\0"),
            (0x260, b"KERNEL32.dll\0"),
        ):
            image[offset : offset + len(name)] = name
        return image

    def read(self, image):
        from windows_metadata import imported_libraries

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "launcher.exe"
            path.write_bytes(image)
            return imported_libraries(path)

    def test_external_crt_import_is_detected_without_loading_binary(self):
        self.assertEqual(self.read(self.image()), ("vcruntime140.dll", "kernel32.dll"))

    def test_import_address_outside_sections_is_rejected(self):
        image = self.image()
        struct.pack_into("<I", image, 0x98 + 120, 0x9000)
        from core import Failure

        with self.assertRaisesRegex(Failure, "outside its sections"):
            self.read(image)

    def test_truncated_header_is_rejected(self):
        from core import Failure

        with self.assertRaisesRegex(Failure, "Expected a PE"):
            self.read(b"MZ")


class OpenCodeFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / ".github/ci").mkdir(parents=True)
        (self.root / "provider-policy").mkdir()
        (self.root / "provider-policy/manifest.toml").write_text(
            '[providers.opencode.cli]\ninstall_version = "2.2.5"\n'
        )
        self.output = self.root / "fixture"

    def archive(self, duplicate=False):
        binary = b"fixture executable"
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as bundle:
            for _ in range(2 if duplicate else 1):
                entry = tarfile.TarInfo("package/bin/opencode")
                entry.size = len(binary)
                bundle.addfile(entry, io.BytesIO(binary))
        data = stream.getvalue()
        pins = {
            "schema": 1,
            "version": "2.2.5",
            "artifacts": {
                opencode_fixture.host(): {
                    "url": "https://registry.npmjs.org/fixture.tgz",
                    "integrity": "sha512-"
                    + base64.b64encode(hashlib.sha512(data).digest()).decode(),
                    "member": "package/bin/opencode",
                }
            },
        }
        (self.root / ".github/ci/opencode-fixtures.json").write_text(json.dumps(pins))
        return data, binary

    def prepare(self, data, version="2.2.5"):
        with (
            patch.object(opencode_fixture.urllib.request, "build_opener") as opener,
            patch.object(
                opencode_fixture.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    [], 0, "opencode v" + version + "\n", ""
                ),
            ),
        ):
            opener.return_value.open.return_value = io.BytesIO(data)
            return opencode_fixture.prepare(self.output, self.root)

    def test_only_verified_versioned_binary_is_promoted(self):
        data, binary = self.archive()
        proof = self.prepare(data)
        name = "opencode.exe" if os.name == "nt" else "opencode"
        self.assertEqual((self.output / name).read_bytes(), binary)
        self.assertEqual(proof["executable_sha256"], hashlib.sha256(binary).hexdigest())

    def test_changed_archive_is_rejected_before_executable_is_written(self):
        data, _ = self.archive()
        with self.assertRaisesRegex(ValueError, "integrity mismatch"):
            self.prepare(data + b"changed")
        self.assertFalse(self.output.exists())

    def test_duplicate_native_executable_is_rejected(self):
        data, _ = self.archive(duplicate=True)
        with self.assertRaisesRegex(ValueError, "one regular executable"):
            self.prepare(data)
        self.assertFalse(self.output.exists())

    def test_provider_policy_drift_fails_before_download(self):
        data, _ = self.archive()
        (self.root / "provider-policy/manifest.toml").write_text(
            '[providers.opencode.cli]\ninstall_version = "2.2.6"\n'
        )
        with self.assertRaisesRegex(ValueError, "match the provider policy"):
            self.prepare(data)

    def test_wrong_executable_version_is_not_promoted(self):
        data, _ = self.archive()
        with self.assertRaisesRegex(ValueError, "version differs"):
            self.prepare(data, version="2.2.4")
        self.assertFalse(self.output.exists())

    def test_existing_output_is_preserved(self):
        data, _ = self.archive()
        self.output.mkdir()
        (self.output / "keep").write_text("unrelated")
        with self.assertRaisesRegex(ValueError, "fresh"):
            self.prepare(data)
        self.assertEqual((self.output / "keep").read_text(), "unrelated")


class ReleaseSafetyTests(unittest.TestCase):
    def test_stale_beta_notes_cannot_be_bundled_with_a_new_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docs").mkdir()
            (root / "docs/beta-release-notes.md").write_text(
                "# Manager Suite 0.1.0-beta candidate\n"
            )
            with self.assertRaisesRegex(ValueError, "Add release notes"):
                ci_release.release_notes("agent-manager", "0.2.0-beta", root)

    def test_product_specific_notes_support_independent_versions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "docs/release-notes/agent-manager/0.2.0-beta.md"
            path.parent.mkdir(parents=True)
            path.write_text("# Agent Manager 0.2.0-beta\n")
            self.assertEqual(
                ci_release.release_notes("agent-manager", "0.2.0-beta", root), path
            )

    def test_unicode_prefix_survives_a_legacy_windows_pipe_encoding(self):
        def legacy_pipe(*arguments):
            code = (
                "import sys; sys.stdout.reconfigure(encoding='cp1252'); "
                "sys.prefix = 'C:/software/\\u4e16\\u754c'; " + arguments[-1]
            )
            return subprocess.run(
                [sys.executable, "-I", "-c", code],
                capture_output=True,
                text=True,
                check=True,
            )

        self.assertEqual(
            qualify_product_installation.installed_prefix(sys.executable, legacy_pipe),
            "C:/software/世界",
        )

    def test_wheel_selection_provisions_pip_for_a_clean_build_interpreter(self):
        def clean_interpreter(*arguments, capture=False):
            if arguments[:2] == ("uv", "export"):
                return ""
            self.assertEqual(arguments[:2], ("uv", "run"))
            self.assertIn("--no-project", arguments)
            self.assertIn("--with", arguments)
            self.assertIn("pip==26.2", arguments)
            return json.dumps({"tags": [], "selected": []})

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(product_payload, "prepare"),
            patch.object(product_payload, "run", side_effect=clean_interpreter),
        ):
            product_payload.dependencies(
                Path(temporary), "windows-x86_64-msvc", "prompt-manager"
            )

    def test_patch_requests_check_the_patch_and_minor_requests_accept_patches(self):
        self.assertTrue(ci_product.matches_python("3.12.14", "3.12"))
        self.assertTrue(ci_product.matches_python("3.12.14", "3.12.14"))
        self.assertFalse(ci_product.matches_python("3.12.13", "3.12.14"))
        self.assertFalse(ci_product.matches_python("3.11.14", "3.12"))

    def test_payload_changes_require_a_new_version_per_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current, history = root / "current", root / "history"
            current.mkdir()
            history.mkdir()
            record = {
                "schema": 1,
                "target": "linux-x86_64-gnu",
                "products": {
                    "manager-suite/prompt-manager": {
                        "version": "0.1.0-beta",
                        "release": "a" * 64,
                    }
                },
            }
            (history / "old.json").write_text(json.dumps(record))
            candidate = current / "suite-payload-identities.json"
            candidate.write_text(json.dumps(record))
            release_identity.check(current, history)
            record["products"]["manager-suite/prompt-manager"]["release"] = "b" * 64
            candidate.write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, "bump the product version"):
                release_identity.check(current, history)
            record["target"] = "macos-arm64"
            candidate.write_text(json.dumps(record))
            release_identity.check(current, history)
            record["target"] = "linux-x86_64-gnu"
            record["products"]["manager-suite/prompt-manager"]["version"] = (
                "0.1.0-beta.1"
            )
            candidate.write_text(json.dumps(record))
            release_identity.check(current, history)


class PlanningTests(unittest.TestCase):
    def test_agent_change_checks_consumers_but_not_unrelated_managers(self):
        self.assertEqual(
            ci_plan.affected(["agent-manager/agent_manager/manager.py"]),
            ["agent-manager", "conversation-manager"],
        )

    def test_conversation_change_does_not_check_agent(self):
        self.assertEqual(
            ci_plan.affected(
                ["conversation-manager/src/conversation_manager/client.py"]
            ),
            ["conversation-manager"],
        )

    def test_prompt_change_checks_skill(self):
        self.assertEqual(
            ci_plan.affected(["prompt-manager/src/prompt_manager/manager.py"]),
            ["prompt-manager", "skill-manager"],
        )

    def test_docs_only_change_has_no_product_matrix(self):
        self.assertEqual(
            ci_plan.plan(paths=["README.md", "agent-manager/docs/python-sdk.md"]),
            {"include": []},
        )

    def test_prompt_assets_are_product_inputs(self):
        self.assertIn(
            "prompt-manager",
            ci_plan.affected(["prompt-manager/packs/agents/sections/coding.md"]),
        )

    def test_shared_inputs_recheck_all_products(self):
        for path in (
            "installation.toml",
            "installer/uv.lock",
            "scripts/dev/releases.py",
            ".github/workflows/product.yml",
        ):
            with self.subTest(path=path):
                self.assertEqual(set(ci_plan.affected([path])), set(ci_plan.PRODUCTS))

    def test_manual_product_does_not_expand_to_every_consumer(self):
        matrix = ci_plan.plan("agent-manager", full=True)
        self.assertEqual(
            matrix["include"],
            [
                {"product": "agent-manager", "python": "3.11"},
                {"product": "agent-manager", "python": "3.14"},
            ],
        )

    def test_missing_base_checks_all_and_deletions_are_detected(self):
        self.assertEqual(
            len(ci_plan.plan(paths=None)["include"]), len(ci_plan.PRODUCTS)
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(
                ["git", "-C", str(root), "config", "user.name", "CI Fixture"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(root), "config", "user.email", "ci@example.invalid"],
                check=True,
            )
            (root / "agent-manager").mkdir()
            file = root / "agent-manager/module.py"
            file.write_text("old = True\n")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(root), "commit", "-qm", "fixture"], check=True
            )
            file.unlink()
            paths = ci_plan.changed_paths("HEAD", "", root)
            self.assertIsNone(paths)
            previous = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
            ).strip()
            subprocess.run(["git", "-C", str(root), "add", "-u"], check=True)
            subprocess.run(
                ["git", "-C", str(root), "commit", "-qm", "delete fixture"], check=True
            )
            paths = ci_plan.changed_paths(previous, "HEAD", root)
            self.assertEqual(paths, ["agent-manager/module.py"])
            self.assertIn("agent-manager", ci_plan.affected(paths))


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / ".github/ci").mkdir(parents=True)
        (self.root / ".github/ci/dependencies.json").write_text(
            '{"schema": 1, "packages": {}}'
        )
        manifest = ["[groups.manager-suite]", 'version = "1.0.0"']
        requirements = ["[dependency-groups]"]
        for index, product in enumerate(ci_plan.PRODUCTS):
            version = f"0.{index + 1}.0"
            distribution, module = ci_release.PACKAGES[product]
            package = self.root / product
            package.mkdir()
            (package / "pyproject.toml").write_text(
                f'[project]\nname = "{distribution}"\nversion = "{version}"\n'
            )
            (package / "src" / module).mkdir(parents=True)
            (package / "src" / module / "_version.py").write_text(
                f'__version__ = "{version}"\n'
            )
            manifest += [
                f'[products."manager-suite/{product}"]',
                f'version = "{version}"',
            ]
            requirements += [f'{product} = ["{distribution}=={version}"]']
        (self.root / "installation.toml").write_text("\n".join(manifest) + "\n")
        (self.root / "installer").mkdir()
        (self.root / "installer/pyproject.toml").write_text(
            "\n".join(requirements) + "\n"
        )

    def test_product_tag_selects_one_release(self):
        product = "agent-manager"
        version = tomllib.loads((self.root / "installation.toml").read_text())[
            "products"
        ][f"manager-suite/{product}"]["version"]
        tag = f"aixand-agent-manager/v{version}"
        self.assertEqual(
            ci_release.selection(tag=tag, root=self.root), (product, version, tag)
        )
        with self.assertRaisesRegex(ValueError, "Tag must match"):
            ci_release.selection(tag="aixand-agent-manager/v99.0.0", root=self.root)
        with self.assertRaisesRegex(ValueError, "Unknown product"):
            ci_release.selection(tag="v1.0.0", root=self.root)

    def test_suite_can_contain_different_product_versions(self):
        with patch.dict(ci_release.PATHS, {name: name for name in ci_plan.PRODUCTS}):
            ci_release.validate_versions("manager-suite", self.root)
        self.assertEqual(
            ci_release.selection("manager-suite", root=self.root)[1], "1.0.0"
        )

    def test_runtime_or_lock_version_mismatch_blocks_release(self):
        path = self.root / "agent-manager/src/agent_manager/_version.py"
        path.write_text('__version__ = "99.0.0"\n')
        with self.assertRaisesRegex(ValueError, "Align agent-manager"):
            ci_release.validate_versions("agent-manager", self.root)
        path.write_text('__version__ = "0.4.0"\n')
        # Find the fixture's version rather than depending on profile ordering.
        version = tomllib.loads(
            (self.root / "agent-manager/pyproject.toml").read_text()
        )["project"]["version"]
        path.write_text(f'__version__ = "{version}"\n')
        lock = self.root / "installer/pyproject.toml"
        lock.write_text(
            lock.read_text().replace(
                f"aixand-agent-manager=={version}", "aixand-agent-manager==99.0.0"
            )
        )
        with self.assertRaisesRegex(ValueError, "installer dependency pin"):
            ci_release.validate_versions("agent-manager", self.root)

    def test_prerelease_versions_map_to_python_versions(self):
        for release, python in [
            ("0.1.0-beta", "0.1.0b0"),
            ("0.2.0-beta.3", "0.2.0b3"),
            ("1.0.0-rc.1", "1.0.0rc1"),
            ("2.0.1", "2.0.1"),
        ]:
            self.assertEqual(ci_release.python_version(release), python)
        for invalid in ("latest", "01.2.3", "0.1", "0.1.0-beta.01"):
            with self.assertRaises(ValueError):
                ci_release.python_version(invalid)

    def test_releases_require_no_external_product_source_checkout(self):
        sources = ci_release.source_outputs(
            "agent-manager", release=True, root=self.root
        )
        self.assertNotIn("mcps-needed", sources)
        self.assertTrue(
            all(
                value == "false"
                for key, value in sources.items()
                if key.endswith("-needed")
            )
        )

    def test_unchanged_libraries_are_not_checked_out_for_prompt_checks(self):
        sources = ci_release.source_outputs("prompt-manager", root=self.root)
        self.assertEqual(sources["libraries-needed"], "false")

    def test_archive_contains_bootstrap_proofs_and_target(self):
        for target in ("linux-x86_64-gnu", "windows-x86_64-msvc"):
            with self.subTest(target=target):
                artifact = self.root / target
                handoff = artifact / "handoff/products/agent-manager"
                handoff.mkdir(parents=True)
                version = ci_release.selection("agent-manager", root=self.root)[1]
                notes = self.root / "docs/release-notes" / (version + ".md")
                notes.parent.mkdir(parents=True, exist_ok=True)
                notes.write_text("# Agent Manager " + version + "\n")
                (handoff / "product-descriptor.json").write_text(
                    json.dumps(
                        {
                            "target": target,
                            "version": version,
                            "selection": "manager-suite/agent-manager",
                            "verification": {"runtime": True},
                            "manifest_sha256": "c" * 64,
                            "products": {
                                "manager-suite/agent-manager": {
                                    "version": version,
                                    "release": "d" * 64,
                                }
                            },
                        }
                    )
                )
                (artifact / "bootstrap").mkdir()
                (artifact / "bootstrap/bootstrap.sh").write_text("fixture\n")
                (artifact / "lifecycle.json").write_text(
                    json.dumps(
                        {"passed": True, "target": target, "manifest_sha256": "c" * 64}
                    )
                )
                output = self.root / ("output-" + target)
                with (
                    patch.object(
                        ci_release, "qualify_archive", return_value={"passed": True}
                    ) as qualification,
                    patch.object(ci_release, "verify_sources", return_value={}),
                    patch.object(
                        ci_release.subprocess, "check_output", return_value="b" * 40
                    ),
                ):
                    ci_release.archive(
                        "agent-manager",
                        artifact,
                        output,
                        self.root,
                        target_expected=target,
                    )
                qualification.assert_called_once()
                self.assertTrue(list(output.glob("*-archive-qualification.json")))
                self.assertTrue(list(output.glob("*-payload-identities.json")))
                if target.startswith("windows"):
                    with zipfile.ZipFile(next(output.glob("*.zip"))) as bundle:
                        names = bundle.namelist()
                else:
                    with tarfile.open(next(output.glob("*.tar.gz"))) as bundle:
                        names = bundle.getnames()
                        for member in bundle.getmembers():
                            self.assertEqual((member.uid, member.gid), (0, 0))
                            self.assertEqual((member.uname, member.gname), ("", ""))
                self.assertTrue(
                    any(name.endswith("/bootstrap/bootstrap.sh") for name in names)
                )
                self.assertTrue(
                    any(name.endswith("/reports/lifecycle.json") for name in names)
                )
                self.assertTrue(any(name.endswith("/INSTALL.md") for name in names))
                self.assertTrue(
                    any(name.endswith("/RELEASE-NOTES.md") for name in names)
                )
                if target.startswith("windows"):
                    with zipfile.ZipFile(next(output.glob("*.zip"))) as bundle:
                        guide = bundle.read(
                            next(name for name in names if name.endswith("/INSTALL.md"))
                        ).decode("utf-8")
                else:
                    with tarfile.open(next(output.glob("*.tar.gz"))) as bundle:
                        guide = (
                            bundle.extractfile(
                                next(
                                    name
                                    for name in names
                                    if name.endswith("/INSTALL.md")
                                )
                            )
                            .read()
                            .decode("utf-8")
                        )
                member = next(
                    name for name in names if name.endswith("/RELEASE-NOTES.md")
                )
                if target.startswith("windows"):
                    with zipfile.ZipFile(next(output.glob("*.zip"))) as bundle:
                        bundled_notes = bundle.read(member)
                else:
                    with tarfile.open(next(output.glob("*.tar.gz"))) as bundle:
                        bundled_notes = bundle.extractfile(member).read()
                self.assertEqual(bundled_notes, notes.read_bytes())
                self.assertIn("(RELEASE-NOTES.md)", guide)
                self.assertNotIn("(beta-release-notes.md)", guide)
                with self.assertRaisesRegex(ValueError, "architecture"):
                    ci_release.archive(
                        "agent-manager",
                        artifact,
                        self.root / "wrong-target",
                        self.root,
                        target_expected="wrong",
                    )


class InstallerSelectionTests(unittest.TestCase):
    def test_one_product_never_builds_the_aggregate_or_other_products(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(build_installers, "run") as run,
        ):
            build_installers.build(Path(directory) / "release", "agent-manager")
        calls = [call.args for call in run.call_args_list]
        packages = [call for call in calls if call[:2] == ("manager_dev.py", "package")]
        self.assertEqual(len(packages), 1)
        self.assertEqual(packages[0][2:4], ("--product", "manager-suite/agent-manager"))
        self.assertTrue(any(call[0] == "qualify_single_product.py" for call in calls))
        self.assertFalse(
            any(call[0] == "qualify_product_installation.py" for call in calls)
        )

    def test_suite_still_qualifies_aggregate_and_individual_identity(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(build_installers, "run") as run,
            patch.object(
                build_installers.subprocess,
                "check_output",
                return_value="/fixture/python",
            ),
        ):
            build_installers.build(Path(directory) / "release")
        calls = [call.args for call in run.call_args_list]
        selected = [
            call
            for call in calls
            if call[:2] == ("manager_dev.py", "package") and "--component" not in call
        ]
        self.assertEqual(len(selected), len(ci_plan.PRODUCTS) + 1)
        self.assertTrue(
            any(call[0] == "qualify_product_installation.py" for call in calls)
        )
        self.assertFalse(any(call[0] == "qualify_single_product.py" for call in calls))

    def test_existing_release_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "keep").write_text("original")
            with self.assertRaisesRegex(RuntimeError, "immutable"):
                build_installers.build(output, "agent-manager")
            self.assertEqual((output / "keep").read_text(), "original")


class WorkflowTests(unittest.TestCase):
    def test_reusable_workflow_inputs_match_callers_and_pr_triggers_are_absent(self):
        import yaml

        paths = ROOT / ".github/workflows"
        reusable = yaml.load(
            (paths / "product.yml").read_text(), Loader=yaml.BaseLoader
        )
        accepted = set(reusable["on"]["workflow_call"]["inputs"])
        for path in paths.glob("*.yml"):
            workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
            self.assertNotIn("pull_request", workflow["on"], path)
            self.assertNotIn("pull_request_target", workflow["on"], path)
            for job in workflow["jobs"].values():
                if job.get("uses") == "./.github/workflows/product.yml":
                    self.assertLessEqual(set(job.get("with", {})), accepted)
        release = yaml.load(
            (paths / "installer.yml").read_text(), Loader=yaml.BaseLoader
        )
        self.assertEqual(
            set(release["jobs"]["prepare"]["needs"]),
            {"plan", "installers", "tooling", "security", "policy"},
        )
        self.assertEqual(
            release["jobs"]["publish"]["concurrency"]["group"],
            "manager-release-publication",
        )
        self.assertEqual(
            release["jobs"]["tooling"]["uses"],
            "./.github/workflows/repository-checks.yml",
        )
        self.assertEqual(release["concurrency"]["cancel-in-progress"], "false")
        publisher = release["jobs"]["pypi"]
        self.assertEqual(
            publisher["environment"], "pypi-${{ needs.plan.outputs.product }}"
        )
        self.assertEqual(publisher["permissions"]["id-token"], "write")
        self.assertEqual(publisher["needs"], ["plan", "prepare"])
        self.assertEqual(
            release["jobs"]["publish"]["needs"], ["plan", "prepare", "pypi"]
        )
        self.assertIn(
            "matrix.product == needs.plan.outputs.product",
            release["jobs"]["checks"]["with"]["python-artifact"],
        )


if __name__ == "__main__":
    unittest.main()
