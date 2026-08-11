"""Regression tests for the whole-project audit-fix round: the fail-open / fail-closed / collision
defects found across common, check, build, normalize. Each test fails against the pre-fix code."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from klode.lib import build, check, normalize                # noqa: E402
from klode.lib.common import Marker, haystacks, resolve       # noqa: E402
from klode.lib.config import Config, ConfigError              # noqa: E402


def _lib(tmp: Path, shelves=("books",), guard=False) -> Path:
    root = tmp / "kb"
    (root / "library" / "cards").mkdir(parents=True)
    for s in shelves:
        (root / "library" / s).mkdir(parents=True)
    (root / "library.toml").write_text(
        f'[library]\nid = "k"\ndir = "library"\ncards = "cards"\nshelves = {list(shelves)!r}\n'
        "[bibliography]\nenabled = false\n"
        f"[copyright]\nguard = {str(guard).lower()}\n", encoding="utf-8")
    return root


class AuditHardening(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-audit-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # common.py:236 — the `#n` pin must be honoured on a literal-with-context anchor (was fail-open)
    def test_nth_honored_on_literal_with_context(self):
        hays = haystacks("the cat sat. the cat ran.")        # "cat" occurs twice, each after "the"
        self.assertTrue(resolve(Marker("cat", before="the", nth=2), hays).found)   # 2 exist
        self.assertFalse(resolve(Marker("cat", before="the", nth=3), hays).found)  # only 2 -> #3 fails

    # build.py — same filename stem on two shelves collides onto one card; must fail loud
    def test_build_rejects_duplicate_stems_across_shelves(self):
        root = _lib(self.tmp, shelves=("books", "papers"))
        (root / "library" / "books" / "plato.txt").write_text("a", encoding="utf-8")
        (root / "library" / "papers" / "plato.txt").write_text("b", encoding="utf-8")
        with self.assertRaises(ConfigError):
            build.build(Config.load(root / "library.toml"))

    # check.py — the copyright-leak guard must fail CLOSED on a git error, N/A only on "not a repo"
    def test_leak_guard_fails_closed_on_git_error_but_na_on_no_repo(self):
        cfg = Config.load(_lib(self.tmp, guard=True) / "library.toml")

        def _fake(returncode, stderr):
            m = mock.Mock(); m.returncode, m.stdout, m.stderr = returncode, "", stderr
            return m

        r = check.Report()
        with mock.patch.object(check.subprocess, "run",
                               return_value=_fake(128, "fatal: detected dubious ownership in repository")):
            check._check_copyright_leak(cfg, r)
        self.assertTrue(r.errors)                            # git errored -> ERROR, not silent skip

        r2 = check.Report()
        with mock.patch.object(check.subprocess, "run",
                               return_value=_fake(128, "fatal: not a git repository (or any parent)")):
            check._check_copyright_leak(cfg, r2)
        self.assertFalse(r2.errors)                          # genuine non-repo -> N/A note only
        self.assertTrue(r2.notes)

    # normalize.py — prune must never delete the run just written (keep clamps to >= 1)
    def test_prune_keeps_newest_even_when_keep_zero(self):
        root = self.tmp / "backups"
        root.mkdir()
        older = root / "normalize-backup-old"
        newer = root / "normalize-backup-new"
        for d in (older, newer):
            d.mkdir()
        import os
        os.utime(older, (1, 1)); os.utime(newer, (2, 2))     # newer has the later mtime
        pruned = normalize.prune_backups(str(root), 0)
        self.assertTrue(newer.is_dir())                      # the freshest run survives
        self.assertIn(str(older), pruned)

class BackupStampCannotEscapeTheBackupRoot(unittest.TestCase):
    """`--stamp` is user input and lands directly in a path.

    `os.path.join(root, f"normalize-backup-{stamp}")` with `../../../../tmp/x` normalises straight
    out of the backup root, so the copies of a copyrighted corpus this function exists to protect
    could be written anywhere the process can write.
    """

    def setUp(self):
        import shutil, tempfile
        from klode.lib.config import Config
        REPO = Path(__file__).resolve().parent.parent
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-stamp-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        kb = self.tmp / "kb"
        shutil.copytree(REPO / "tests/fixtures/kb-fixture", kb)
        self.cfg = Config.load(kb / "library.toml")

    def test_a_traversing_stamp_is_refused(self):
        for bad in ("../../../../../../tmp/klode-escape", "..", ".", "a/b",
                    "/absolute", "x" * 65, "with space", "nul\x00byte"):
            with self.subTest(stamp=bad):
                with self.assertRaises(ValueError):
                    normalize.normalize(self.cfg, apply=False, stamp=bad)

    def test_an_empty_stamp_falls_back_to_the_generated_one(self):
        # "" is falsy, so it means "unspecified" rather than "invalid" — asserted so the
        # distinction is deliberate rather than incidental
        normalize.normalize(self.cfg, apply=False, stamp="")

    def test_an_ordinary_stamp_is_accepted(self):
        # the guard must not refuse the documented use
        for good in ("20260811-204500", "run_1", "a.b-c"):
            with self.subTest(stamp=good):
                normalize.normalize(self.cfg, apply=False, stamp=good)

if __name__ == "__main__":
    unittest.main()
