"""A shelf name reaches `git ls-files` as a pathspec, and a pathspec is not a name.

Two ways that bit. `git ls-files -z <paths>` had no `--`, so a shelf called `--others` was consumed
as an OPTION: git listed untracked files, returned 0, and the guard reported no leak while a
copyrighted source sat tracked in the index. And git pathspecs glob by default, so a shelf called
`books*` also matched a sibling `booksOTHER/`, reporting files outside every guarded shelf as
leaks. (A bracketed shelf resolves either way — git prefix-matches a directory pathspec — so the
wildcard case, not the bracket, is what `--literal-pathspecs` is actually here for.)

This is the guard whose failure mode is publishing someone else's book, so it is closed twice — at
the call site with `--` and `--literal-pathspecs`, and at the config boundary, which no longer
accepts a shelf that can look like a flag.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from klode.lib import check                                              # noqa: E402
from klode.lib.config import Config, ConfigError                         # noqa: E402


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


class _Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        _git("init", "-q", ".", cwd=self.tmp)
        _git("config", "user.email", "t@example.invalid", cwd=self.tmp)
        _git("config", "user.name", "t", cwd=self.tmp)

    def _kb(self, shelf: str, *, dirname: str = "library") -> Config:
        base = self.tmp / dirname if dirname else self.tmp
        (base / shelf).mkdir(parents=True, exist_ok=True)
        (base / "cards").mkdir(parents=True, exist_ok=True)
        (self.tmp / "library.toml").write_text(
            f'[library]\nid = "t"\ndir = "{dirname}"\nshelves = ["{shelf}"]\n'
            '[bibliography]\nenabled = false\n', encoding="utf-8")
        return Config.load(self.tmp / "library.toml")


class ATrackedCorpusFileIsAlwaysReported(_Repo):
    def test_a_shelf_named_like_a_git_option_does_not_blind_the_guard(self):
        """`--others` made git list untracked files instead of the guarded path, rc 0, no leak
        reported. Config now refuses the name outright — the guard is not the last line."""
        with self.assertRaises(ConfigError) as cm:
            self._kb("--others", dirname="")
        self.assertIn("must not begin with", str(cm.exception))

    def test_a_composed_pathspec_that_looks_like_a_flag_is_refused(self):
        """The first guard checked raw shelf NAMES, which left the composed path unguarded:
        `[library].dir = "--format="` with an ordinary shelf produced the pathspec
        `--format=/books`, which git accepts as an option and which hides every tracked file.
        Whatever assembles it, the pathspec is what must not look like a flag."""
        (self.tmp / "--format=" / "books").mkdir(parents=True)
        (self.tmp / "library.toml").write_text(
            '[library]\nid = "t"\ndir = "--format="\nshelves = ["books"]\n', encoding="utf-8")
        with self.assertRaises(ConfigError) as cm:
            Config.load(self.tmp / "library.toml")
        self.assertIn("leading", str(cm.exception).lower() + " leading")

    def test_a_leading_dash_is_refused_in_extra_guard_dirs_too(self):
        (self.tmp / "library" / "books").mkdir(parents=True)
        (self.tmp / "library.toml").write_text(
            '[library]\nid = "t"\ndir = "library"\nshelves = ["books"]\n'
            '[copyright]\nextra_guard_dirs = ["--others"]\n', encoding="utf-8")
        with self.assertRaises(ConfigError) as cm:
            Config.load(self.tmp / "library.toml")
        self.assertIn("must not begin with", str(cm.exception))

    def test_a_tracked_txt_under_a_normal_shelf_is_caught(self):
        cfg = self._kb("books")
        leak = self.tmp / "library" / "books" / "leak.txt"
        leak.write_text("copyrighted", encoding="utf-8")
        _git("add", "-f", "library/books/leak.txt", cwd=self.tmp)
        r = check.check(cfg)
        self.assertTrue(any("copyright-leak" in e for e in r.errors),
                        f"a tracked corpus file went unreported; errors were {r.errors}")

    def test_a_tracked_txt_under_a_BRACKETED_shelf_is_caught(self):
        """A bracketed shelf resolves either way — git prefix-matches a directory pathspec — but
        the guard must keep working for it, since `glob_in` now makes such a KB usable at all."""
        cfg = self._kb("books[1]")
        leak = self.tmp / "library" / "books[1]" / "leak.txt"
        leak.write_text("copyrighted", encoding="utf-8")
        _git("add", "-f", "library/books[1]/leak.txt", cwd=self.tmp)
        r = check.check(cfg)
        self.assertTrue(any("copyright-leak" in e for e in r.errors),
                        f"a tracked file under a bracketed shelf went unreported: {r.errors}")

    def test_a_wildcard_shelf_does_not_drag_in_its_siblings(self):
        """The measured effect of --literal-pathspecs. A shelf literally named `books*` matched a
        sibling `booksOTHER/` as well, so unrelated tracked files were reported as copyright
        leaks. A guard that cries wolf is a guard that gets ignored."""
        cfg = self._kb("books*")
        (self.tmp / "library" / "booksOTHER").mkdir(parents=True)
        (self.tmp / "library" / "books*" / "real.txt").write_text("corpus", encoding="utf-8")
        (self.tmp / "library" / "booksOTHER" / "unrelated.txt").write_text("mine", encoding="utf-8")
        _git("add", "-f", "library/books*/real.txt", "library/booksOTHER/unrelated.txt",
             cwd=self.tmp)
        r = check.check(cfg)
        leaks = [e for e in r.errors if "copyright-leak" in e]
        self.assertTrue(any("books*/real.txt" in e for e in leaks), f"real leak missed: {leaks}")
        self.assertFalse(any("booksOTHER" in e for e in leaks),
                         f"a file outside every guarded shelf was reported as a leak: {leaks}")

    def test_a_clean_repo_reports_no_leak(self):
        """The guard must still be quiet when there is nothing to report, or the tests above pass
        for the wrong reason."""
        cfg = self._kb("books")
        (self.tmp / "library" / "books" / "ok.txt").write_text("local only", encoding="utf-8")
        r = check.check(cfg)
        self.assertFalse(any("copyright-leak" in e for e in r.errors))


class TheGuardCannotBeRedirectedOrOutrun(_Repo):
    def test_an_inherited_git_index_cannot_redirect_the_guard(self):
        """`GIT_INDEX_FILE` is the sharp one: `rev-parse` still answers "yes, a work tree", and
        then `ls-files` reads the ALTERNATE index and finds nothing. Verified against this repo —
        with it set to a nonexistent path the guard returned errors=[], notes=[], ok=True while
        corpus files were tracked in the real index."""
        cfg = self._kb("books")
        (self.tmp / "library" / "books" / "leak.txt").write_text("copyrighted", encoding="utf-8")
        _git("add", "-f", "library/books/leak.txt", cwd=self.tmp)
        old = os.environ.get("GIT_INDEX_FILE")
        os.environ["GIT_INDEX_FILE"] = str(self.tmp / "nonexistent.index")
        self.addCleanup(lambda: os.environ.pop("GIT_INDEX_FILE", None)
                        if old is None else os.environ.__setitem__("GIT_INDEX_FILE", old))
        r = check.check(cfg)
        self.assertTrue(any("copyright-leak" in e for e in r.errors),
                        f"an inherited GIT_INDEX_FILE hid a tracked leak; errors were {r.errors}")

    def test_a_missing_git_binary_is_unmeasured_not_a_clean_pass(self):
        """Absence of the BINARY says nothing about absence of the REPOSITORY. Reported as N/A this
        read as "nothing to leak into" while a checkout with tracked corpus files sat right there."""
        from unittest import mock
        cfg = self._kb("books")
        with mock.patch("subprocess.run", side_effect=FileNotFoundError("no git")):
            r = check.check(cfg)
        self.assertTrue(any("git is not on PATH" in u for u in r.unmeasured))
        self.assertFalse(r.ok, "a guard that could not run reported ok")

    def test_the_in_tree_backup_sweep_matches_what_the_normalizer_writes(self):
        """`normalize.py` creates `normalize-backup-<stamp>` with NO leading dot; the sweep looked
        for `.normalize-backup-*` and therefore matched nothing, ever. A guard that has never fired
        looks exactly like a guard that is working."""
        cfg = self._kb("books")
        for name in ("normalize-backup-20260814", ".normalize-backup-20260101"):
            with self.subTest(name=name):
                d = cfg.lib / name
                d.mkdir()
                try:
                    r = check.check(cfg)
                    self.assertTrue(any("normalize backup" in e for e in r.errors),
                                    f"{name} went unreported; errors were {r.errors}")
                finally:
                    d.rmdir()


class TheGuardStillFailsClosed(_Repo):
    def test_a_failing_ls_files_is_an_error_not_a_pass(self):
        """Fail-closed still holds after the argv change. Only `ls-files` is made to fail — the
        preceding `rev-parse` must run normally, or the test would be exercising a different path
        (and that call deliberately catches only FileNotFoundError)."""
        cfg = self._kb("books")
        real = subprocess.run

        def selective(argv, *a, **kw):
            if "ls-files" in argv:
                raise subprocess.SubprocessError("boom")
            return real(argv, *a, **kw)

        with unittest.mock.patch("subprocess.run", side_effect=selective):
            r = check.check(cfg)
        self.assertTrue(any("must not fail open" in e for e in r.errors),
                        f"a failing git call did not fail closed; errors were {r.errors}")

    def test_the_argv_places_the_main_option_before_the_subcommand(self):
        """`--literal-pathspecs` is a MAIN git option. Placed after `ls-files` it exits 129, and
        every copyright-guarded KB errors on every run — which is how I first wrote it."""
        cfg = self._kb("books")
        seen = []
        real = subprocess.run

        def record(argv, *a, **kw):
            if "ls-files" in argv:
                seen.append(list(argv))
            return real(argv, *a, **kw)

        with unittest.mock.patch("subprocess.run", side_effect=record):
            check.check(cfg)
        self.assertTrue(seen, "ls-files was never invoked")
        argv = seen[0]
        self.assertLess(argv.index("--literal-pathspecs"), argv.index("ls-files"))
        self.assertLess(argv.index("ls-files"), argv.index("--"))
        self.assertEqual(argv[argv.index("--") + 1:], list(cfg.guard_relpaths))


if __name__ == "__main__":
    unittest.main()
