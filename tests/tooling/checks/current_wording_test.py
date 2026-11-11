"""Keep documentation and source commentary focused on current behavior."""

import ast
import importlib.util
import io
import os
from pathlib import Path
import re
import sys
import tokenize
import unittest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "current_wording_docs", ROOT / "scripts/checks/check-docs.py"
)
DOCS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = DOCS
SPEC.loader.exec_module(DOCS)

HISTORY_LABEL = re.compile(
    r"\blegacy\b|\bschema[- ](?:one|two|three|1|2|3)\b", re.IGNORECASE
)
SKIP = {"node_modules", "__pycache__", "build", "dist"}


def wording_issue(text):
    # Literal API names and paths document executable contracts.
    return HISTORY_LABEL.search(DOCS.mask_inline_code(text))


def source_files():
    for directory in (
        "scripts",
        "tests",
        *sorted(p.name for p in ROOT.glob("*-manager")),
    ):
        for base, directories, files in os.walk(ROOT / directory):
            directories[:] = [
                d for d in directories if not d.startswith(".") and d not in SKIP
            ]
            for name in files:
                if name.endswith(".py"):
                    yield Path(base) / name


class CurrentWordingTests(unittest.TestCase):
    def test_documentation_describes_current_behavior(self):
        config = DOCS.load_config(ROOT)
        _, pages = DOCS.validate(ROOT, config)
        for page in pages:
            for line, prose in page.prose:
                with self.subTest(path=str(page.path.relative_to(ROOT)), line=line):
                    self.assertIsNone(wording_issue(prose), prose)

    def test_python_comments_and_docstrings_describe_current_behavior(self):
        for path in source_files():
            source = path.read_text(encoding="utf-8")
            passages = [
                (token.start[0], token.string)
                for token in tokenize.generate_tokens(io.StringIO(source).readline)
                if token.type == tokenize.COMMENT
            ]
            for node in ast.walk(ast.parse(source)):
                if isinstance(
                    node,
                    (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
                ):
                    docstring = ast.get_docstring(node)
                    if docstring:
                        passages.append((node.body[0].lineno, docstring))
            for line, passage in passages:
                with self.subTest(path=str(path.relative_to(ROOT)), line=line):
                    self.assertIsNone(wording_issue(passage), passage)

    def test_installer_help_uses_product_terms(self):
        path = ROOT / "scripts/dev/cli.py"
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.keyword)
                and node.arg == "help"
                and isinstance(node.value, ast.Constant)
            ):
                self.assertIsNone(wording_issue(node.value.value), node.value.value)

    def test_api_identifiers_and_schema_data_are_allowed(self):
        self.assertIsNone(wording_issue("Use `adopt_legacy()` or `--legacy-name`."))
        self.assertIsNone(wording_issue("The manifest stores `schema = 2`."))
        self.assertIsNone(
            wording_issue(
                "Retired environments cannot launch; historical revisions remain readable."
            )
        )
        self.assertIsNotNone(wording_issue("Use the schema-2 installer."))
        self.assertIsNotNone(wording_issue("A legacy registry handles this operation."))


if __name__ == "__main__":
    unittest.main()
