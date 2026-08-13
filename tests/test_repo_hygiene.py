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

class CliProseGoesThroughTheSanitiser(unittest.TestCase):
    """`klode/lib/cli.py` shadows `print` so every prose line is sanitised at ONE boundary.

    The first attempt at this fix patched four call sites by hand and immediately missed a fifth —
    `cmd_verify` printed raw source lines exactly as `cmd_zoom` had. Per-site vigilance produced
    that gap and would keep producing it. These tests fail if the shadow is removed, or if a call
    site reaches around it.
    """

    def test_the_module_shadows_print(self):
        import klode.lib.cli as cli
        self.assertIsNot(cli.print, __builtins__["print"] if isinstance(__builtins__, dict)
                         else __builtins__.print)
        self.assertEqual(cli.print.__module__, "klode.lib.cli")

    def test_no_call_site_reaches_around_the_shadow(self):
        import ast
        src = (REPO / "klode" / "lib" / "cli.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        shadow = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == "print")
        inside = range(shadow.lineno, (shadow.end_lineno or shadow.lineno) + 1)
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            # `_builtin_print(...)` is legitimate ONLY inside the shadow's own body
            if isinstance(f, ast.Name) and f.id == "_builtin_print" and node.lineno not in inside:
                offenders.append(f"cli.py:{node.lineno}: _builtin_print outside the shadow")
            if isinstance(f, ast.Attribute) and f.attr == "print" \
                    and isinstance(f.value, ast.Name) and f.value.id in ("builtins", "sys"):
                offenders.append(f"cli.py:{node.lineno}: {f.value.id}.print bypasses the shadow")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_the_sanitiser_neuters_what_it_must_and_keeps_what_it_must(self):
        from klode.lib.cli import sane
        for hostile in ("\x1b[2J\x1b[H", "\x1b]0;title\x07", "\x9b31m", "\x7f", "\x00"):
            with self.subTest(repr(hostile)):
                out = sane(f"a{hostile}b")
                self.assertNotIn("\x1b", out)
                self.assertNotIn("\x9b", out)
                self.assertNotIn("\x7f", out)
                self.assertTrue(out.startswith("a") and out.endswith("b"))
        # legitimate content is untouched, including tabs and every script klode supports
        for ok in ("plain ascii", "a\tb", "café — 世界 · Ελληνικά · русский", "emoji 🎯", "«quoted»"):
            with self.subTest(ok):
                self.assertEqual(sane(ok), ok)

    def test_newlines_survive_because_prose_is_multi_line(self):
        # stripping \n turned every `print("\n" + body)` into one run-on line
        from klode.lib.cli import sane
        self.assertEqual(sane("line one\nline two"), "line one\nline two")
        self.assertEqual(sane("\nleading and trailing\n"), "\nleading and trailing\n")

    def test_a_bare_carriage_return_is_not_treated_as_layout(self):
        # `\r` without `\n` returns the cursor to the start of the line so following text
        # overwrites what was printed — a spoofing primitive, not layout
        from klode.lib.cli import sane
        self.assertNotIn("\r", sane("real line\rFAKE"))
        self.assertIn("real line", sane("real line\rFAKE"))

if __name__ == "__main__":
    unittest.main()
