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
    TAG_ONLY_GUARD = re.compile(
        r"startsWith\(\s*github\.ref\s*,\s*(['\"])refs/tags/v\1\s*\)")

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
        condition = guard.group(1).strip()
        self.assertIsNotNone(
            self.TAG_ONLY_GUARD.fullmatch(condition),
            f"publish is guarded by {condition!r}, not a positive tag-only predicate")

    def test_tag_guard_rejects_negation_and_non_tag_alternatives(self):
        unsafe = ("!startsWith(github.ref, 'refs/tags/v')",
                  "startsWith(github.ref, 'refs/tags/v') || github.event_name == 'workflow_dispatch'")
        for condition in unsafe:
            with self.subTest(condition=condition):
                self.assertIsNone(self.TAG_ONLY_GUARD.fullmatch(condition))


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
        hardcoded = re.search(r"prog\s*=\s*(['\"])klode\1", src)
        self.assertIsNone(hardcoded, "the program name is spelled twice; it can drift again")

    def test_the_duplicate_name_guard_recognizes_both_quote_styles(self):
        pattern = re.compile(r"prog\s*=\s*(['\"])klode\1")
        for source in ('prog="klode"', "prog = 'klode'"):
            with self.subTest(source=source):
                self.assertIsNotNone(pattern.search(source))

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

    @staticmethod
    def _offenders(src, label="module.py"):
        """Every `.tmp` path literal must belong to mkstemp, regardless of expression syntax."""
        offenders = []
        lines = src.splitlines()
        tree = ast.parse(src)
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

        def belongs_to_mkstemp(node):
            while node in parents:
                node = parents[node]
                if isinstance(node, ast.Call):
                    func = node.func
                    return ((isinstance(func, ast.Attribute) and func.attr == "mkstemp")
                            or (isinstance(func, ast.Name) and func.id == "mkstemp"))
            return False

        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and node.value.endswith(".tmp") and not belongs_to_mkstemp(node)):
                line = lines[node.lineno - 1].strip() if node.lineno <= len(lines) else ""
                offenders.append(f"{label}:{node.lineno}: {line[:70]}")
        return offenders

    def test_no_module_builds_a_temp_path_by_string_concatenation(self):
        offenders = []
        for path in sorted(REPO.glob("klode/**/*.py")):
            offenders.extend(self._offenders(path.read_text(encoding="utf-8"),
                                             str(path.relative_to(REPO))))
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_an_earlier_mkstemp_does_not_exempt_a_later_concatenation(self):
        src = ('fd, tmp = tempfile.mkstemp(suffix=".tmp")\n'
               'candidate = target.with_name(target.name + ".tmp")\n')
        self.assertEqual(len(self._offenders(src)), 1)

    def test_quote_and_spacing_variants_are_equally_rejected(self):
        variants = ("candidate = target.with_name(target.name + '.tmp')",
                    'path = str(target)+".tmp"')
        for src in variants:
            with self.subTest(src=src):
                self.assertEqual(len(self._offenders(src)), 1)

    def test_multiline_and_f_string_variants_are_equally_rejected(self):
        variants = ('candidate = target.with_name(\n    target.name +\n    ".tmp"\n)',
                    'candidate = f"{target}.tmp"')
        for src in variants:
            with self.subTest(src=src):
                self.assertEqual(len(self._offenders(src)), 1)

    def test_percent_and_dot_format_variants_are_equally_rejected(self):
        variants = ('candidate = "%s.tmp" % target',
                    'candidate = "{}.tmp".format(target)')
        for src in variants:
            with self.subTest(src=src):
                self.assertEqual(len(self._offenders(src)), 1)

    def test_with_suffix_variant_is_rejected(self):
        self.assertEqual(len(self._offenders('candidate = target.with_suffix(".tmp")')), 1)

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

    @staticmethod
    def _bypasses(src, label="cli.py"):
        tree = ast.parse(src)
        shadows = [n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "print"]
        inside = {line for shadow in shadows
                  for line in range(shadow.lineno, (shadow.end_lineno or shadow.lineno) + 1)}
        offenders = []
        builtin_modules = {"builtins"}
        raw_names = {"_builtin_print"}

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "builtins":
                        builtin_modules.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module == "builtins":
                for alias in node.names:
                    if alias.name == "print":
                        bound = alias.asname or alias.name
                        raw_names.add(bound)
                        if bound != "_builtin_print":
                            offenders.append(f"{label}:{node.lineno}: imports raw print as {bound}")

        def raw_ref(node):
            if isinstance(node, ast.Name) and node.id in raw_names:
                return True
            if isinstance(node, ast.Attribute) and node.attr == "print" \
                    and isinstance(node.value, ast.Name) and node.value.id in builtin_modules:
                return True
            return (isinstance(node, ast.Attribute) and node.attr == "write"
                    and isinstance(node.value, ast.Attribute) and node.value.attr == "stdout"
                    and isinstance(node.value.value, ast.Name) and node.value.value.id == "sys")

        for node in ast.walk(tree):
            values = []
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                values = [node.value]
            if values and any(raw_ref(value) for value in values):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = [target.id for target in targets if isinstance(target, ast.Name)]
                raw_names.update(names)
                offenders.append(f"{label}:{node.lineno}: aliases raw output as {', '.join(names) or '?'}")
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            # `_builtin_print(...)` is legitimate ONLY inside the shadow's own body
            if isinstance(f, ast.Name) and f.id == "_builtin_print" and node.lineno not in inside:
                offenders.append(f"{label}:{node.lineno}: _builtin_print outside the shadow")
            elif raw_ref(f) and not (isinstance(f, ast.Name) and f.id == "_builtin_print"
                                     and node.lineno in inside):
                offenders.append(f"{label}:{node.lineno}: raw output bypasses the shadow")
        return offenders

    def test_no_call_site_reaches_around_the_shadow(self):
        src = (REPO / "klode" / "lib" / "cli.py").read_text(encoding="utf-8")
        offenders = self._bypasses(src)
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_raw_print_aliases_are_rejected(self):
        bypasses = ("from builtins import print as raw\nraw('x')",
                    "import builtins\nraw = builtins.print\nraw('x')",
                    "import sys\nsys.stdout.write('x')")
        for source in bypasses:
            with self.subTest(source=source):
                self.assertTrue(self._bypasses(source))

    def test_the_sanitiser_neuters_what_it_must_and_keeps_what_it_must(self):
        from klode.lib.cli import sane
        for hostile in ("\x1b[2J\x1b[H", "\x1b]0;title\x07", "\x9b31m", "\x7f", "\x00"):
            with self.subTest(repr(hostile)):
                out = sane(f"a{hostile}b")
                self.assertNotIn("\x1b", out)
                self.assertNotIn("\x9b", out)
                self.assertNotIn("\x7f", out)
                self.assertNotIn("\x00", out)
                self.assertNotIn("\x07", out)
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
