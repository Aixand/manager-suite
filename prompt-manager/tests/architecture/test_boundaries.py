"""Keep pure prompt rules, persistence, and transport ownership independent."""

import ast
import importlib.util
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "prompt_manager"


def modules():
    result = {}
    for path in PACKAGE.rglob("*.py"):
        parts = list(path.relative_to(PACKAGE).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        result[".".join(["prompt_manager", *parts])] = (path, ast.parse(path.read_text()))
    return result


def imports(module, path, tree):
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = importlib.util.resolve_name("." * node.level + base, package)
            yield base
            yield from (base + "." + alias.name for alias in node.names)


class ArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (PACKAGE / "manager.py").is_file():
            raise AssertionError(f"Architecture source tree is unavailable: {PACKAGE}")

    def test_prompt_engine_and_storage_depend_only_on_their_owners(self):
        for module, (path, tree) in modules().items():
            if module.startswith("prompt_manager.prompts"):
                allowed = (
                    "prompt_manager.prompts",
                    "prompt_manager.codec",
                    "prompt_manager.errors",
                )
            elif module.startswith("prompt_manager.storage"):
                allowed = (
                    "prompt_manager.storage",
                    "prompt_manager._paths",  # Shared location policy, with no storage access.
                    "prompt_manager.codec",
                    "prompt_manager.errors",
                )
            else:
                continue
            for dependency in imports(module, path, tree):
                if dependency.startswith("prompt_manager."):
                    with self.subTest(module=module, dependency=dependency):
                        self.assertTrue(dependency.startswith(allowed))

    def test_domain_implementations_do_not_import_the_facade_or_interfaces(self):
        for module, (path, tree) in modules().items():
            if module.split(".")[1:2] not in [
                [owner]
                for owner in ("prompts", "library", "applications", "managed", "sources", "storage")
            ]:
                continue
            for dependency in imports(module, path, tree):
                with self.subTest(module=module, dependency=dependency):
                    self.assertNotEqual("prompt_manager.manager", dependency)
                    self.assertNotEqual("prompt_manager.Manager", dependency)
                    self.assertFalse(dependency.startswith("prompt_manager.interfaces"))

    def test_domain_operations_do_not_accept_the_manager_facade(self):
        owners = {"prompts", "library", "applications", "managed", "sources", "storage"}
        for module, (_, tree) in modules().items():
            if module.split(".")[1:2] and module.split(".")[1] in owners:
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                        with self.subTest(module=module, operation=node.name):
                            self.assertNotIn("manager", [argument.arg for argument in args])
        _, facade = modules()["prompt_manager.manager"]
        domains = {
            "authoring",
            "backup",
            "catalog",
            "comparison",
            "discovery",
            "releases",
            "snapshots",
            "resolution",
            "managed_authoring",
            "inspection",
            "organization",
            "integrity",
            "registration",
            "search",
            "workspace",
        }
        for node in ast.walk(facade):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in domains
            ):
                values = [*node.args, *(keyword.value for keyword in node.keywords)]
                self.assertFalse(
                    any(isinstance(value, ast.Name) and value.id == "self" for value in values),
                    ast.unparse(node),
                )

    def test_library_content_operations_do_not_depend_on_catalog_aggregation(self):
        definitions = modules()
        for owner in ("snapshots", "discovery", "authoring", "comparison", "releases"):
            module = "prompt_manager.library." + owner
            path, tree = definitions[module]
            for dependency in imports(module, path, tree):
                with self.subTest(module=module, dependency=dependency):
                    self.assertFalse(
                        dependency.startswith(
                            (
                                "prompt_manager.applications",
                                "prompt_manager.managed",
                                "prompt_manager.library.catalog",
                            )
                        )
                    )

    def test_runtime_imports_are_standard_library_or_prompt_manager(self):
        import sys

        for module, (path, tree) in modules().items():
            for dependency in imports(module, path, tree):
                root = dependency.split(".")[0]
                with self.subTest(module=module, dependency=dependency):
                    self.assertIn(root, sys.stdlib_module_names | {"prompt_manager"})

    def test_explicit_sdk_depends_only_on_contracts_and_errors(self):
        module = "prompt_manager.interfaces.sdk"
        path, tree = modules()[module]
        for dependency in imports(module, path, tree):
            if dependency.startswith("prompt_manager."):
                self.assertTrue(
                    dependency.startswith(
                        (
                            "prompt_manager.interfaces.contracts",
                            "prompt_manager.errors",
                        )
                    ),
                    dependency,
                )
        # Domain policy belongs behind public Manager methods, not handle callbacks.
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute):
                if node.value.attr == "_manager":
                    self.assertFalse(node.attr.startswith("_"), node.attr)

    def test_package_initializers_do_not_forward_implementations(self):
        for module, (path, tree) in modules().items():
            if path.name == "__init__.py" and module != "prompt_manager":
                with self.subTest(module=module):
                    self.assertFalse(
                        any(isinstance(n, (ast.Import, ast.ImportFrom)) for n in tree.body)
                    )

    def test_internal_import_graph_has_no_cycles(self):
        definitions = modules()
        edges = {
            module: set(imports(module, path, tree)) & definitions.keys()
            for module, (path, tree) in definitions.items()
        }
        visited, active = set(), []

        def visit(module):
            if module in active:
                self.fail("Import cycle: " + " -> ".join([*active, module]))
            if module in visited:
                return
            active.append(module)
            for dependency in edges[module]:
                visit(dependency)
            active.pop()
            visited.add(module)

        for module in definitions:
            visit(module)
