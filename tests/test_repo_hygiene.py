"""Repo-wide structural guards for defect classes that have recurred.

A defect that appears three times is not three mistakes, it is one missing check. Both guards here
exist because the same fault was fixed individually more than twice.
"""
import ast
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


class MainGuardIsLast(unittest.TestCase):
    """`if __name__ == "__main__": unittest.main()` placed before a class silently skips it when
    the file is run directly. pytest still collects it, so the omission is invisible in CI — which
    is exactly how it recurred three times (test_criterion_spec, test_rate, test_ingest_integrity).
    """

    def test_no_test_file_defines_a_class_after_its_main_guard(self):
        offenders = []
        for path in sorted((REPO / "tests").glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            guard_line = None
            for node in tree.body:
                if isinstance(node, ast.If) and ast.unparse(node.test).startswith("__name__"):
                    guard_line = node.lineno
                elif guard_line is not None and isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                    offenders.append(f"{path.name}: {node.name} defined after the main guard "
                                     f"(line {node.lineno} > {guard_line})")
        self.assertEqual(offenders, [], "\n".join(offenders))


class TempFilesAreUnpredictable(unittest.TestCase):
    """A temp path derived from its target is guessable, and opening it follows a symlink planted
    there. Fixed once in gate/__main__.py, then found again in lib/ingest.py."""

    def test_no_module_builds_a_temp_path_by_string_concatenation(self):
        offenders = []
        for path in sorted(REPO.glob("klode/**/*.py")):
            src = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(src.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if '.tmp"' in stripped and "mkstemp" not in src[:src.find(line)] + line:
                    if "with_name" in stripped or "+ \".tmp\"" in stripped:
                        offenders.append(f"{path.relative_to(REPO)}:{lineno}: {stripped[:70]}")
        self.assertEqual(offenders, [], "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
