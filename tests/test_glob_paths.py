"""A library path is not a pattern.

`glob.glob(os.path.join(cfg.cards, "*.md"))` reads `[ ] * ?` anywhere in the joined string, the
DIRECTORY prefix included. A knowledge base under `.../kb[1]/` — or with `shelves = ["books[1]"]`,
which config validation accepts — therefore enumerated zero cards and zero sources.

That is not merely an empty result. `check` records `unmeasured` only `if cards:`, so an empty card
list is the one shape that slips past the guard built to stop `abstained` reading as `verified`:
the run printed `OK: 0 errors` and exited 0 over a citation resolving nowhere, and `build` rewrote
INDEX.md to empty and exited 0 as well.

The frameworks/syntheses layer uses `Path.glob` and was never affected, so the KB half-worked —
`consult` rendered a full panel while `search` said "no matching cards". Nothing looked broken.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from klode.lib import build, check                                       # noqa: E402
from klode.lib.common import card_files, glob_in, shelf_txts             # noqa: E402
from klode.lib.config import Config                                      # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "kb-fixture"


class _KBAtAHostilePath(unittest.TestCase):
    """The fixture KB, copied to a directory whose name is a glob pattern."""

    DIRNAME = "kb[1]"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.kb = self.tmp / self.DIRNAME
        shutil.copytree(FIXTURE, self.kb)
        self.cfg = Config.load(self.kb / "library.toml")

    def _break_a_citation(self) -> str:
        card = self.kb / "library" / "cards" / "brevity.md"
        text = card.read_text(encoding="utf-8")
        marker = "(grep: `"
        i = text.index(marker) + len(marker)
        j = text.index("`", i)
        original = text[i:j]
        card.write_text(text[:i] + "ZZZ-THIS-QUOTE-IS-NOWHERE" + text[j:], encoding="utf-8")
        return original


class TheCorpusIsVisibleAtAMetacharacterPath(_KBAtAHostilePath):
    def test_cards_are_enumerated(self):
        self.assertTrue(card_files(self.cfg), "no cards found under a `[`-containing library path")

    def test_shelf_sources_are_enumerated(self):
        self.assertTrue(shelf_txts(self.cfg), "no shelf sources found under a `[`-containing path")

    def test_the_counts_match_the_same_kb_at_a_plain_path(self):
        plain = self.tmp / "plain"
        shutil.copytree(FIXTURE, plain)
        ref = Config.load(plain / "library.toml")
        self.assertEqual(len(card_files(self.cfg)), len(card_files(ref)))
        self.assertEqual(len(shelf_txts(self.cfg)), len(shelf_txts(ref)))


class CitationRotIsStillCaughtAtAMetacharacterPath(_KBAtAHostilePath):
    def test_a_broken_citation_fails_the_check(self):
        self._break_a_citation()
        r = check.check(self.cfg)
        self.assertFalse(r.ok, "check passed over a citation that resolves nowhere")
        self.assertTrue(any("citation-rot" in e for e in r.errors),
                        f"no citation-rot error raised; got {r.errors}")

    def test_an_intact_kb_reports_its_real_card_count(self):
        r = check.check(self.cfg)
        self.assertGreater(r.n_cards, 0)
        self.assertGreater(r.n_txts, 0)
        self.assertFalse(any("corpus not installed" in n for n in r.notes),
                         "an installed corpus was reported as absent")


class BuildDoesNotEmptyTheIndex(_KBAtAHostilePath):
    def test_build_sees_the_cards(self):
        index = self.kb / "library" / "cards" / "INDEX.md"
        before = index.read_text(encoding="utf-8")
        build.build(self.cfg)
        after = index.read_text(encoding="utf-8")
        self.assertIn("brevity", after, "build rewrote INDEX.md without the cards on disk")
        self.assertEqual(before.count("](" ), after.count("]("),
                         "build changed the number of indexed cards")


class AShelfNameIsAlsoAPattern(unittest.TestCase):
    """`[library].shelves` validation rejects only `/`, `""`, `.` and `..` — a shelf may legally
    carry a metacharacter, so escaping the library dir alone would be an incomplete fix."""

    def test_a_bracketed_shelf_still_enumerates(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "library" / "books[1]").mkdir(parents=True)
        (tmp / "library" / "cards").mkdir(parents=True)
        (tmp / "library.toml").write_text(
            '[library]\nid = "t"\ndir = "library"\nshelves = ["books[1]"]\n', encoding="utf-8")
        (tmp / "library" / "books[1]" / "a.txt").write_text("text", encoding="utf-8")
        cfg = Config.load(tmp / "library.toml")
        self.assertEqual(cfg.shelves, ("books[1]",))
        self.assertEqual(len(shelf_txts(cfg)), 1,
                         "a shelf whose name contains `[` enumerated nothing")


class GlobInEscapesDirectoriesButNotThePattern(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_the_trailing_pattern_stays_live(self):
        d = self.tmp / "d[1]"
        d.mkdir()
        for n in ("a.md", "b.md", "c.txt"):
            (d / n).write_text("x", encoding="utf-8")
        self.assertEqual(sorted(os.path.basename(p) for p in glob_in(d, "*.md")), ["a.md", "b.md"])

    def test_dotfiles_stay_excluded_as_glob_excludes_them(self):
        """`Path.glob` would return these. An editor's `.#card.md` lock file is a symlink to
        `user@host.pid`; enumerating it as a card is how the swap-to-pathlib fix breaks `read`."""
        d = self.tmp / "d[2]"
        d.mkdir()
        (d / "real.md").write_text("x", encoding="utf-8")
        (d / ".#real.md").write_text("x", encoding="utf-8")
        self.assertEqual([os.path.basename(p) for p in glob_in(d, "*.md")], ["real.md"])

    def test_an_explicitly_dotted_pattern_still_matches(self):
        """check.py's `.normalize-backup-*` sweep depends on this."""
        d = self.tmp / "d[3]"
        d.mkdir()
        (d / ".normalize-backup-1").mkdir()
        self.assertEqual(len(glob_in(d, ".normalize-backup-*")), 1)

    def test_intermediate_segments_are_escaped_too(self):
        (self.tmp / "a[1]" / "b[2]").mkdir(parents=True)
        (self.tmp / "a[1]" / "b[2]" / "x.txt").write_text("x", encoding="utf-8")
        self.assertEqual(len(glob_in(self.tmp, "a[1]", "b[2]", "*.txt")), 1)


class TheDirectoryNeverEntersThePattern(unittest.TestCase):
    """`glob.escape` was the first fix and was two-thirds of one. It turns `[category]` into
    `[[]category]` — still a live bracket expression — so glob must LIST the parent to match it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.d = self.tmp / "[category]"
        self.d.mkdir()
        for n in ("a.md", "b.md"):
            (self.d / n).write_text("x", encoding="utf-8")

    def test_a_traversable_but_unlistable_parent_still_enumerates(self):
        """A parent that can be entered but not listed made the escaped form return [] with no
        error — the same silent-empty failure the whole fix exists to remove, by another route."""
        from unittest import mock
        parent, real = str(self.tmp), os.scandir

        def deny(path):
            if os.fspath(path) == parent:
                raise PermissionError("execute-only parent")
            return real(path)

        with mock.patch("glob.os.scandir", side_effect=deny):
            found = glob_in(self.d, "*.md")
        self.assertEqual(len(found), 2,
                         "enumeration went empty because the parent could not be listed")

    def test_an_absolute_pattern_is_passed_through_not_silently_dropped(self):
        """`os.path.join` discards the base for an absolute pattern, so escaping the base
        protected nothing in that case. The behaviour must at least match the original."""
        import glob as _glob
        absolute = str(self.d / "*.md")
        self.assertEqual(sorted(glob_in(self.d, absolute)), sorted(_glob.glob(absolute)))


class BuildRefusesToRewriteFromABlindEnumeration(unittest.TestCase):
    """`check` only reports; `build` REWRITES INDEX.md, so the write side is where an enumeration
    failure becomes irreversible. The existing guard covers "no sources but cards exist"; it
    cannot see the case where BOTH enumerations come back empty because enumeration itself
    failed — which is exactly what the metacharacter path did."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        shutil.copytree(FIXTURE, self.tmp / "kb")
        self.cfg = Config.load(self.tmp / "kb" / "library.toml")

    def test_it_refuses_rather_than_emptying_the_board(self):
        from unittest import mock
        from klode.lib.config import ConfigError
        index = self.cfg.cards / "INDEX.md"
        before = index.read_text(encoding="utf-8")
        with mock.patch("klode.lib.build.glob_in", return_value=[]):
            with self.assertRaises(ConfigError) as cm:
                build.build(self.cfg)
        self.assertIn("refusing to rewrite", str(cm.exception))
        self.assertEqual(index.read_text(encoding="utf-8"), before,
                         "the board was rewritten despite the refusal")

    def test_a_genuinely_empty_cards_directory_still_builds(self):
        """The guard must not block the legitimate first build of a new library."""
        for p in self.cfg.cards.glob("*.md"):
            p.unlink()
        self.assertEqual(build.build(self.cfg)["total"], 2)

    def test_an_unreadable_cards_directory_refuses_rather_than_crashing_mid_write(self):
        from unittest import mock
        from klode.lib.config import ConfigError
        with mock.patch("klode.lib.build.glob_in", return_value=[]), \
             mock.patch("os.scandir", side_effect=PermissionError("locked")):
            with self.assertRaises(ConfigError):
                build.build(self.cfg)


class NormalizeDoesNotCertifyWhatItDidNotRead(unittest.TestCase):
    """`glob` turns a directory it cannot read into ZERO matches with no error, so a shelf that
    went unreadable left `scanned` truthy and `skipped` empty — and `normalize --check` certified
    a corpus it had only partly read. Verified before the fix: 1 of 2 files scanned, exit 0."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        shutil.copytree(FIXTURE, self.tmp / "kb")
        self.cfg = Config.load(self.tmp / "kb" / "library.toml")

    def test_an_unlistable_shelf_is_recorded(self):
        from unittest import mock
        from klode.lib import normalize
        real = os.scandir

        def deny(path, *a, **k):
            if str(path).endswith("books"):
                raise PermissionError("locked shelf")
            return real(path, *a, **k)

        with mock.patch("os.scandir", side_effect=deny):
            res = normalize.normalize(self.cfg, pattern="*/*.txt")
        self.assertTrue(res.unreadable, "an unlistable shelf was scanned over in silence")

    def test_an_absolute_pattern_is_refused(self):
        """`glob` ignores `root_dir` for an absolute pattern, which puts the library prefix back
        into the pattern string and re-arms every metacharacter in it."""
        from klode.lib import normalize
        res = normalize.normalize(self.cfg, pattern=str(self.cfg.lib / "*" / "*.txt"))
        self.assertIn("absolute", res.refused or "")

    def test_the_ordinary_scan_is_unaffected(self):
        from klode.lib import normalize
        res = normalize.normalize(self.cfg, pattern="*/*.txt")
        self.assertEqual(res.scanned, 2)
        self.assertFalse(res.unreadable)
        self.assertIsNone(res.refused)


class BuildRefusesEveryBlindEnumeration(unittest.TestCase):
    """Three enumerations feed `build`, and all three could fail open independently. The first
    guard covered cards only — a partial SHELF scan stays truthy, and a swallowed FRAMEWORK error
    makes build write `framework: none` over links that exist."""

    def _kb(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        shutil.copytree(FIXTURE, tmp / "kb")
        return Config.load(tmp / "kb" / "library.toml")

    def test_a_partial_shelf_scan_is_refused(self):
        from unittest import mock
        from klode.lib.config import ConfigError
        cfg = self._kb()
        real = build.glob_in
        with mock.patch("klode.lib.build.glob_in",
                        side_effect=lambda d, *p: real(d, *p)[:1] if "books" in str(p)
                        else real(d, *p)):
            with self.assertRaises(ConfigError) as cm:
                build.build(cfg)
        self.assertIn("source enumeration missed", str(cm.exception))

    def test_a_blind_framework_scan_is_refused_before_it_rewrites_metadata(self):
        from unittest import mock
        from klode.lib.config import ConfigError
        cfg = self._kb()
        (cfg.frameworks / "vega.md").write_text(
            "# Vega\n\n**Source:** `library/books/brevity.txt`\n", encoding="utf-8")
        real = build.glob_in
        with mock.patch("klode.lib.build.glob_in",
                        side_effect=lambda d, *p: [] if str(d).endswith("frameworks")
                        else real(d, *p)):
            with self.assertRaises(ConfigError) as cm:
                build.build(cfg)
        self.assertIn("framework enumeration missed", str(cm.exception))

    def test_a_normal_build_still_succeeds(self):
        self.assertEqual(build.build(self._kb())["total"], 2)


class TheEnumeratorTripwire(unittest.TestCase):
    """Fixing the cause removes today's failure; the tripwire is what stops a future cause from
    being silent again."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        shutil.copytree(FIXTURE, self.tmp / "kb")
        self.cfg = Config.load(self.tmp / "kb" / "library.toml")

    def test_it_fires_when_enumeration_disagrees_with_the_directory(self):
        from unittest import mock
        with mock.patch("klode.lib.check.card_files", return_value=[]):
            r = check.check(self.cfg)
        self.assertTrue(any("disagrees with the" in e for e in r.errors),
                        f"tripwire did not fire; errors were {r.errors}")

    def test_it_does_not_fire_on_a_directory_holding_only_index_and_readme(self):
        for p in self.cfg.cards.glob("*.md"):
            if p.name not in ("INDEX.md", "README.md"):
                p.unlink()
        (self.cfg.cards / "README.md").write_text("# cards\n", encoding="utf-8")
        r = check.check(self.cfg)
        self.assertFalse(any("disagrees with the" in e for e in r.errors),
                         "tripwire false-fired on a genuinely card-less directory")

    def test_it_fires_on_PARTIAL_enumeration_not_only_on_zero(self):
        """A zero-vs-nonzero test caught the total failure and nothing else. Enumerate one card of
        two and the run reported ok=True, errors=[], unmeasured=[] — while never reading the card
        it skipped. Partial blindness is the worse shape: the number on screen looks plausible."""
        from unittest import mock
        real = check.card_files
        both = real(self.cfg)
        self.assertGreaterEqual(len(both), 2, "fixture needs >=2 cards for this test")
        with mock.patch("klode.lib.check.card_files", side_effect=lambda c: real(c)[:1]):
            r = check.check(self.cfg)
        self.assertTrue(any("enumeration missed" in e for e in r.errors),
                        f"partial enumeration went unreported; errors were {r.errors}")
        self.assertFalse(r.ok)

    def test_it_does_not_fire_on_a_hidden_md_file(self):
        """`scandir` sees dotfiles; `glob`'s `*.md` does not. Comparing the two sets without
        matching that rule made an editor lock file (`.#card.md`) look like a missed card."""
        (self.cfg.cards / ".draft.md").write_text("# hidden\n", encoding="utf-8")
        r = check.check(self.cfg)
        self.assertFalse(any("enumeration missed" in e for e in r.errors),
                         f"tripwire false-fired on a hidden file; errors were {r.errors}")

    def test_it_covers_the_frameworks_and_syntheses_layers_too(self):
        """The frameworks and syntheses loops had no guard of their own — `glob` turns a
        directory-read error into `[]`, and neither loop records an error or `unmeasured`, so an
        entire enabled citation layer could be skipped in silence. A tripwire protecting one of
        three enumerators is the same defect with better odds."""
        from unittest import mock
        (self.cfg.frameworks / "vega.md").write_text(
            "# Vega\n\n**Dimension:** Pacing\n\n### 1. Engine\n\nx\n", encoding="utf-8")
        self.assertTrue(check.check(self.cfg).ok, "fixture should be clean before blinding")
        real = check.glob_in

        def blind(d, *parts):
            return [] if str(d).endswith("frameworks") else real(d, *parts)

        with mock.patch("klode.lib.check.glob_in", side_effect=blind):
            r = check.check(self.cfg)
        self.assertTrue(any("framework enumeration missed" in e for e in r.errors),
                        f"a blinded frameworks layer went unreported; errors were {r.errors}")
        self.assertFalse(r.ok)

    def test_it_does_not_fire_on_a_genuinely_empty_directory(self):
        for p in self.cfg.cards.glob("*"):
            p.unlink()
        r = check.check(self.cfg)
        self.assertFalse(any("disagrees with the" in e for e in r.errors))


if __name__ == "__main__":
    unittest.main()
