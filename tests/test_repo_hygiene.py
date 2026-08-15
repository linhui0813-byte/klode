"""Repo-wide structural guards for defect classes that have recurred.

A defect that appears three times is not three mistakes, it is one missing check. Both guards here
exist because the same fault was fixed individually more than twice.
"""
import ast
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


class ReleaseIsGatedOnTheSuite(unittest.TestCase):
    """A tag push publishes to PyPI. `tests.yml` triggers on `push: branches: ["**"]`, and a tag
    ref is not a branch ref — so no release ever ran the suite, the fixture lint, or the
    zero-dependency probe on the ref being published, and `publish` needed only `build`.

    Parsed as TEXT on purpose. CI installs nothing (`python -m unittest`, no pip), so a test that
    imported PyYAML would pass here and be unrunnable in the one place it has to run.
    """

    WF = Path(__file__).resolve().parent.parent / ".github" / "workflows"

    def _jobs(self, name: str) -> dict[str, list[str]]:
        """`job name -> its needs`, from the two-space-indented job block. Enough structure for
        this guard, and no dependency to buy it."""
        lines = (self.WF / name).read_text(encoding="utf-8").splitlines()
        start = next(i for i, l in enumerate(lines) if l.rstrip() == "jobs:")
        jobs: dict[str, list[str]] = {}
        current = None
        for line in lines[start + 1:]:
            if line.strip() and not line.startswith(" "):
                break                                       # a new top-level key ends `jobs:`
            m = re.match(r"^  ([A-Za-z_][\w-]*):\s*$", line)
            if m:
                current = m.group(1)
                jobs[current] = []
            elif current is not None:
                n = re.match(r"^    needs:\s*(.+?)\s*$", line)
                if n:
                    jobs[current] = re.findall(r"[\w-]+", n.group(1))
        return jobs

    def test_publish_depends_transitively_on_the_test_workflow(self):
        jobs = self._jobs("workflow.yml")
        self.assertIn("publish", jobs, "the publish job disappeared")
        seen, frontier = set(), list(jobs.get("publish", []))
        while frontier:
            j = frontier.pop()
            if j in seen:
                continue
            seen.add(j)
            frontier += jobs.get(j, [])
        self.assertTrue(seen & {"tests", "test"},
                        f"publish does not depend on the test suite (reaches {sorted(seen)}) — a "
                        "tag would publish over a red run")

    def test_the_release_gate_calls_the_same_workflow_branches_use(self):
        body = (self.WF / "workflow.yml").read_text(encoding="utf-8")
        self.assertIn("./.github/workflows/tests.yml", body,
                      "the release gate does not reuse tests.yml — a copied job list drifts")

    def test_tests_yml_is_callable(self):
        body = (self.WF / "tests.yml").read_text(encoding="utf-8")
        self.assertRegex(body, r"(?m)^  workflow_call:",
                         "tests.yml has no workflow_call trigger, so publish cannot depend on it")

    def test_the_gated_jobs_are_all_still_there(self):
        """publish depending on `tests` is worth nothing if `tests` stops running the checks."""
        jobs = self._jobs("tests.yml")
        for required in ("test", "corpus", "zero_deps"):
            self.assertIn(required, jobs, f"tests.yml no longer defines the `{required}` job")

    def test_publish_only_ever_runs_for_a_tag(self):
        """`workflow_dispatch` is on the release workflow so a release can be re-run by hand. With
        no ref guard that let anyone who can press "Run workflow" publish an arbitrary branch to
        PyPI — and Trusted Publishing authenticates the workflow, not the operator, so no token
        stands in the way. Same class as the missing test gate: the release path was reachable
        without passing what makes a release a release."""
        body = (self.WF / "workflow.yml").read_text(encoding="utf-8")
        block = body[body.index("  publish:"):]
        guard = re.search(r"^    if:\s*(.+)$", block, re.M)
        self.assertIsNotNone(guard, "the publish job has no `if:` guard — dispatch would publish")
        self.assertIn("refs/tags/", guard.group(1),
                      f"publish is guarded by {guard.group(1)!r}, which does not require a tag")


class PrintedCommandsAreRunnable(unittest.TestCase):
    """Four CLI hints printed `lib consult …`, `lib diagnose …`, `lib zoom …` — the name of the
    tool klode was ported from. They survived review because `lib` exists at ~/.local/bin/lib on
    the machine this was written on, so the hint ran there and only there.

    Matching a command token ANYWHERE in the literal, not just at the start: all four sat
    mid-string behind a label or an arrow, which is why a startswith check would have found none
    of them.

    Scoped to FORMATTED hints — preceded by a column gap, a backtick, or an arrow. A verb at the
    very start of a string is prose far more often than it is a hint ("library check — 2 cards"),
    and a guard that cries wolf on headings gets loosened until it catches nothing. Stated rather
    than hidden: a bare unlabelled hint at string start would slip past this.
    """

    @staticmethod
    def _verbs() -> list[str]:
        """The real subcommand list, read off the parser — so a verb added later is covered without
        anyone remembering to add it here."""
        import klode.lib.cli as cli
        sub = next(a for a in cli.build_parser()._actions
                   if a.__class__.__name__ == "_SubParsersAction")
        return sorted(sub.choices)

    @staticmethod
    def _printed_strings(tree) -> list[tuple[int, str]]:
        """Every string literal that reaches `print(...)`, f-string parts included.

        Scoped to print arguments rather than every constant in the file: a docstring explaining
        the defect ("printed \"no matching cards\"") is not itself a hint, and a guard that flags
        the prose describing a bug is a guard that gets deleted."""
        out = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "print"):
                continue
            for arg in node.args:
                for part in ast.walk(arg):
                    if isinstance(part, ast.Constant) and isinstance(part.value, str):
                        out.append((part.lineno, part.value))
        return out

    def test_no_printed_hint_invokes_a_program_other_than_klode(self):
        import klode.lib.cli as cli
        verbs = self._verbs()
        pattern = re.compile(r"(?:\s\s|`|→\s)([a-z][a-z0-9_.-]*)\s+(" + "|".join(verbs) + r")\b")
        allowed = {cli.PROG, "python3", "python", "pipx", "pip", "uv"}
        src = (REPO / "klode" / "lib" / "cli.py").read_text(encoding="utf-8")
        offenders = []
        for lineno, text in self._printed_strings(ast.parse(src)):
            for prog, verb in pattern.findall(text):
                if prog not in allowed:
                    offenders.append(f"cli.py:{lineno}: prints `{prog} {verb}` — "
                                     f"not {cli.PROG!r}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_the_parser_and_the_hints_share_one_name(self):
        import klode.lib.cli as cli
        self.assertEqual(cli.build_parser().prog, cli.PROG)
        src = (REPO / "klode" / "lib" / "cli.py").read_text(encoding="utf-8")
        self.assertNotIn('prog="klode"', src,
                         "the program name is spelled twice; it can drift again")

    def test_the_guard_catches_the_original_wording(self):
        """A guard nobody has seen fire is a guard nobody knows the shape of."""
        verbs = self._verbs()
        pattern = re.compile(r"(?:\s\s|`|→\s)([a-z][a-z0-9_.-]*)\s+(" + "|".join(verbs) + r")\b")
        for original in ('\nread one:  lib consult <name> [--section spec] [--full]',
                         'stuck?     lib diagnose "what feels wrong"',
                         '\nverify against the source:  lib zoom x --level content',
                         '      → lib consult pacing'):
            with self.subTest(original=original.strip()):
                self.assertEqual([p for p, _ in pattern.findall(original)], ["lib"])


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
