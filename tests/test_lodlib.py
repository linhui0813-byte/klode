"""Proof suite for lodlib. Stdlib unittest, zero dependencies.

Run:  python3 -m unittest discover -s tests   (from the repo root)
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lode.lib import cli, entail, mcp_server, query
from lode.lib.build import build
from lode.lib.check import check
from lode.lib.common import (Marker, body_after_marker, front_matter, grep_markers,
                           haystacks, occurs, parse_markers, resolve)
from lode.lib.config import Config, ConfigError

# Note the deliberate space-wrap: "accepted by\ndefault" spans a hard line break, the exact
# pdftotext failure the citation-rot matcher folds away.
SOURCE = ("The quick brown fox jumps over the lazy dog.\n"
          "Belief is accepted by\ndefault unless actively rejected.\n")


def make_library(tmp: Path, *, guard: bool = True, shelves=("books",)) -> Path:
    """Write a minimal but complete library under `tmp`; return the config path."""
    shelf_list = ", ".join(f'"{s}"' for s in shelves)
    (tmp / "library.toml").write_text(
        "[library]\n"
        'dir = "library"\n'
        'cards = "cards"\n'
        f"shelves = [{shelf_list}]\n"
        "[bibliography]\n"
        "enabled = true\n"
        'path = "BIBLIOGRAPHY.md"\n'
        "[copyright]\n"
        f"guard = {str(guard).lower()}\n",
        encoding="utf-8",
    )
    lib = tmp / "library"
    (lib / shelves[0]).mkdir(parents=True)
    (lib / "BIBLIOGRAPHY.md").write_text(
        "| Foo Bar — A Book | `foo.txt` | a note |\n", encoding="utf-8")
    (lib / shelves[0] / "foo.txt").write_text(SOURCE, encoding="utf-8")
    return tmp / "library.toml"


def set_thin(cfg: Config, sid: str, thin_body: str) -> None:
    """Replace a card's Thin section body (below the scaffold marker)."""
    path = cfg.cards / f"{sid}.md"
    text = path.read_text(encoding="utf-8")
    head = text[: text.index("## Thin")]
    path.write_text(head + f"## Thin\n\n{thin_body}\n\n## Full\n_(owed)_\n", encoding="utf-8")


class TempLibTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lodlib-test-"))
        self.cfg_path = make_library(self.tmp)
        self.cfg = Config.load(self.cfg_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
class ConfigTests(TempLibTest):
    def test_defaults_resolved(self):
        self.assertEqual(self.cfg.shelves, ("books",))
        self.assertEqual(self.cfg.lib_rel, "library")
        self.assertTrue(str(self.cfg.cards).endswith("library/cards"))
        self.assertIn("library/books", self.cfg.guard_relpaths)

    def test_find_walks_up(self):
        deep = self.tmp / "a" / "b"
        deep.mkdir(parents=True)
        self.assertEqual(Config.find(deep), self.cfg_path.resolve())

    def test_missing_config_raises(self):
        with self.assertRaises(ConfigError):
            Config.load(start=Path(tempfile.gettempdir()) / "definitely-no-config-here-xyz")

    def test_empty_shelves_rejected(self):
        (self.tmp / "library.toml").write_text(
            '[library]\ndir = "library"\nshelves = []\n', encoding="utf-8")
        with self.assertRaises(ConfigError):
            Config.load(self.cfg_path)


# ---------------------------------------------------------------------------
class BuildTests(TempLibTest):
    def test_scaffolds_card_and_index(self):
        st = build(self.cfg)
        self.assertEqual(st["created"], 1)
        self.assertTrue((self.cfg.cards / "foo.md").exists())
        self.assertTrue((self.cfg.cards / "INDEX.md").exists())
        card = (self.cfg.cards / "foo.md").read_text(encoding="utf-8")
        self.assertIn("file: library/books/foo.txt", card)
        self.assertIn("**Bibliography.** Foo Bar", card)   # bibliography mirror embedded

    def test_idempotent_and_preserves_body(self):
        build(self.cfg)
        set_thin(self.cfg, "foo", 'The core claim — `grep: "quick brown fox"`.')
        before = (self.cfg.cards / "foo.md").read_text(encoding="utf-8")
        st = build(self.cfg)                       # second run
        after = (self.cfg.cards / "foo.md").read_text(encoding="utf-8")
        self.assertEqual(st["created"], 0)
        self.assertEqual(st["updated"], 1)
        self.assertEqual(before, after)            # hand-written Thin survived, byte-for-byte
        self.assertIn("quick brown fox", after)


# ---------------------------------------------------------------------------
class CheckTests(TempLibTest):
    def test_clean_library_passes(self):
        build(self.cfg)
        set_thin(self.cfg, "foo", 'A grounded claim — `grep: "jumps over the lazy dog"`.')
        r = check(self.cfg)
        self.assertTrue(r.ok, msg=r.errors)

    def test_citation_rot_is_error(self):
        build(self.cfg)
        set_thin(self.cfg, "foo", 'A drifted claim — `grep: "this phrase is not in the source"`.')
        r = check(self.cfg)
        self.assertFalse(r.ok)
        self.assertTrue(any("citation-rot" in e for e in r.errors))

    def test_wrapped_phrase_still_resolves(self):
        # "accepted by default" spans a hard wrap in SOURCE (by\ndefault) — must NOT be flagged as rot.
        build(self.cfg)
        set_thin(self.cfg, "foo", 'Cross-line — `grep: "accepted by default"`.')
        r = check(self.cfg)
        self.assertTrue(r.ok, msg=r.errors)

    def test_orphan_source_is_error(self):
        build(self.cfg)
        (self.cfg.lib / "books" / "orphan.txt").write_text("no card for me", encoding="utf-8")
        r = check(self.cfg)
        self.assertTrue(any("[B orphan]" in e for e in r.errors))

    def test_stale_index_is_error(self):
        build(self.cfg)
        (self.cfg.cards / "INDEX.md").write_text("# stale board with no cards listed\n", encoding="utf-8")
        r = check(self.cfg)
        self.assertTrue(any("[D]" in e for e in r.errors))

    def test_absent_corpus_degrades_not_fails(self):
        build(self.cfg)
        set_thin(self.cfg, "foo", 'A claim — `grep: "jumps over the lazy dog"`.')
        (self.cfg.lib / "books" / "foo.txt").unlink()      # simulate a fresh clone (corpus git-ignored)
        r = check(self.cfg)
        # source-dependent checks are skipped, not failed; but B (source->card) also has nothing to flag
        self.assertTrue(r.ok, msg=r.errors)
        self.assertTrue(any("not installed" in n for n in r.notes))


# ---------------------------------------------------------------------------
class SynthesisFCheckTests(unittest.TestCase):
    """Regression: a synthesis that cites only framework cards must still have its grep
    anchors validated. Framework cards declare their source INLINE (**Source:** `…`), not
    via a front-matter `file:`; the F-check must resolve that, else synthesis citation-rot
    goes unchecked and a fabricated quote slips through as green."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lodlib-fw-test-"))
        (self.tmp / "library.toml").write_text(
            '[library]\ndir = "library"\ncards = "cards"\nshelves = ["books"]\n'
            "[frameworks]\nenabled = true\n"
            '[bibliography]\nenabled = true\npath = "BIBLIOGRAPHY.md"\n'
            "[copyright]\nguard = false\n",
            encoding="utf-8")
        lib = self.tmp / "library"
        (lib / "books").mkdir(parents=True)
        (lib / "cards").mkdir()
        (lib / "BIBLIOGRAPHY.md").write_text("| Foo Bar — A Book | `foo.txt` | note |\n", encoding="utf-8")
        (lib / "books" / "foo.txt").write_text(SOURCE, encoding="utf-8")
        # framework card: source declared INLINE, no front-matter `file:` — exactly the real corpus shape
        (lib / "frameworks").mkdir()
        (lib / "frameworks" / "thinker.md").write_text(
            "# Framework Card — A Thinker\n\n"
            "**Dimension:** Test · **Source:** `library/books/foo.txt`\n\n"
            '### 1. Core engine\nIt says *"jumps over the lazy dog"* (search: `jumps over the lazy dog`).\n',
            encoding="utf-8")
        (lib / "frameworks" / "_syntheses").mkdir()
        self.cfg_path = self.tmp / "library.toml"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _synthesis_with_anchor(self, anchor: str) -> Config:
        syn = self.tmp / "library" / "frameworks" / "_syntheses" / "panel.md"
        syn.write_text(
            "---\ntitle: Synthesis — Test\nstatus: proposed\ndimension: test\ncards: [thinker]\n---\n\n"
            f'# Panel\n\nThe thinker holds a view `(grep: "{anchor}")`.\n', encoding="utf-8")
        return Config.load(self.cfg_path)

    def test_real_quote_resolves_via_framework_inline_source(self):
        r = check(self._synthesis_with_anchor("jumps over the lazy dog"))
        # post-fix the source resolves, so the synthesis draws NO F-warning at all —
        # neither citation-rot nor the "no resolvable source" the pre-fix path emitted.
        self.assertFalse(any("panel.md" in w for w in r.warns), msg=r.warns)

    def test_fabricated_quote_is_flagged(self):
        r = check(self._synthesis_with_anchor("a fabricated phrase absent from every source"))
        self.assertTrue(any("citation-rot" in w for w in r.warns), msg=f"warns={r.warns}")


# ---------------------------------------------------------------------------
class CopyrightGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lodlib-git-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @unittest.skipUnless(shutil.which("git"), "git not available")
    def test_tracked_corpus_is_leak_error(self):
        cfg_path = make_library(self.tmp, guard=True)
        cfg = Config.load(cfg_path)
        build(cfg)
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        for args in (["init", "-q"], ["add", "library/books/foo.txt"],
                     ["commit", "-qm", "leak"]):
            subprocess.run(["git", "-C", str(self.tmp), *args], check=True, env=env,
                           capture_output=True)
        r = check(cfg)
        self.assertTrue(any("copyright-leak" in e for e in r.errors), msg=r.errors)

    def test_outside_git_guard_is_na_not_error(self):
        cfg = Config.load(make_library(self.tmp, guard=True))
        build(cfg)
        r = check(cfg)
        self.assertTrue(r.ok, msg=r.errors)
        self.assertTrue(any("copyright-leak guard N/A" in n for n in r.notes))


# ---------------------------------------------------------------------------
class CommonTests(unittest.TestCase):
    def test_grep_markers_both_conventions(self):
        text = 'a (grep: `first form`) and b (`grep: "second form"`).'
        self.assertEqual(grep_markers(text), ["first form", "second form"])

    def test_ingest_tier_selection(self):
        # auto trusts a clean text layer and never needs the OCR deps; a corrupted one
        # would escalate. Score the two extremes and check the decision boundary.
        from lode.lib.ingest import corruption_score, CLEAN_THRESHOLD
        clean = "the regulation of narrative information is what mood aims at " * 40
        garbled = "the~ regulatIOn of narrative mfonnafion t~e Dist~nce de~ignated " * 40
        self.assertLess(corruption_score(clean), CLEAN_THRESHOLD)
        self.assertGreater(corruption_score(garbled), CLEAN_THRESHOLD)

    def test_strip_page_furniture(self):
        from lode.lib.normalize import strip_page_furniture
        text = ("The narrative can also choose\n"
                "13\n"
                "to regulate the information.\n"
                "II\n"
                ",I' 'Ii\n"
                "Foreword\n\nreal line one\nForeword\n\nreal line two\n"
                "Foreword\nForeword\nForeword\n"
                "I loved her.\n")            # a real sentence starting with the pronoun 'I'
        out, n, _ex = strip_page_furniture(text)
        self.assertNotIn("\n13\n", "\n" + out + "\n")   # page number gone
        self.assertNotIn("II", out.split("\n"))          # roman folio gone
        self.assertNotIn(",I' 'Ii", out)                 # punctuation-noise gone
        self.assertNotIn("Foreword", out)                # running head (≥5×) gone
        self.assertIn("The narrative can also choose", out)   # content kept
        self.assertIn("to regulate the information.", out)    # content kept
        self.assertIn("I loved her.", out)               # pronoun 'I' line kept
        self.assertGreaterEqual(n, 8)

    def test_grep_markers_all_four_key_forms(self):
        # Every anchor form the corpus actually uses must extract the phrase, not a
        # stray space, and `research:` must never be mistaken for a `search:` anchor.
        text = ('A (grep: `alpha`), B (search: `beta`), '
                'C (`grep:` `gamma`), D (`grep: "delta"`); my research: `noise` note')
        self.assertEqual(grep_markers(text), ["alpha", "beta", "gamma", "delta"])

    def test_occurs_folds_smart_quotes_and_hyphens(self):
        # source uses a smart apostrophe (U+2019), a non-breaking hyphen (U+2011), and a
        # Unicode hyphen (U+2010); the ASCII-quoting card marker must still resolve.
        hay = haystacks("the reader’s co‑creation of well‐known facts")
        self.assertTrue(occurs("reader's co-creation", hay))
        self.assertTrue(occurs("well-known facts", hay))

    def test_body_after_marker(self):
        self.assertEqual(body_after_marker("head<!-- x -->tail"), None)

    def test_body_after_marker_recognizes_foreign_tool(self):
        # A card written by another generation of the generator (a different tool name in
        # the marker) MUST still expose its body, or build stubs over hand-written content.
        foreign = ("head\n<!-- scaffold: managed by build_library_cards.py — "
                   "edit Thin/Full below the marker by hand -->\n"
                   "## Thin\nreal content (grep: `x`)\n")
        body = body_after_marker(foreign)
        self.assertIsNotNone(body)
        self.assertIn("real content", body)


# ---------------------------------------------------------------------------
class CliTests(TempLibTest):
    def _run(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["-c", str(self.cfg_path), *argv])
        return code, out.getvalue(), err.getvalue()

    def setUp(self):
        super().setUp()
        build(self.cfg)
        set_thin(self.cfg, "foo",
                 'The fox rationalizes — `grep: "jumps over the lazy dog"`.')
        build(self.cfg)   # refresh board zoom

    def test_search_finds_card(self):
        code, out, _ = self._run("search", "rationalizes", "fox")
        self.assertEqual(code, 0)
        self.assertIn("foo", out)

    def test_zoom_thin(self):
        code, out, _ = self._run("zoom", "foo", "--level", "thin")
        self.assertEqual(code, 0)
        self.assertIn("rationalizes", out)

    def test_zoom_content_grep_verifies(self):
        code, out, _ = self._run("zoom", "foo", "--level", "content",
                                 "--grep", "jumps over the lazy dog")
        self.assertEqual(code, 0)

    def test_zoom_content_grep_rot_fails(self):
        code, _, err = self._run("zoom", "foo", "--level", "content",
                                 "--grep", "not in the source at all")
        self.assertEqual(code, 1)
        self.assertIn("NOT FOUND", err)

    def test_check_via_cli_ok(self):
        code, out, _ = self._run("check")
        self.assertEqual(code, 0)
        self.assertIn("OK", out)


# ---------------------------------------------------------------------------
class MarkerParseTests(unittest.TestCase):
    """P0/P1 — anchors carry a type (literal vs regex) and optional disambiguation."""

    def test_regex_marker_is_typed(self):
        ms = parse_markers('literal (grep: `a.b`) and regex (grep-re: `a.b`)')
        self.assertEqual([(m.phrase, m.regex) for m in ms], [("a.b", False), ("a.b", True)])

    def test_context_and_index_parse(self):
        (m,) = parse_markers('pinned (grep: `foo` before `the` after `bar`)')
        self.assertEqual((m.phrase, m.before, m.after), ("foo", "the", "bar"))
        (n,) = parse_markers('nth (grep: `foo` #3)')
        self.assertEqual((n.phrase, n.nth), ("foo", 3))

    def test_trailing_prose_not_mistaken_for_context(self):
        # the `)` closing an inline marker shields following words like "before"/"after"
        (m,) = parse_markers('X (grep: `foo`) before we go further')
        self.assertEqual((m.before, m.after, m.nth), (None, None, None))

    def test_unparenthesized_outer_backtick_form_does_not_absorb_prose_context(self):
        # `grep: "X"` (no parens) followed by prose "before `Y`" must NOT become before-context
        (m,) = parse_markers('The API returns `grep: "status code"` before `validation` occurs.')
        self.assertEqual((m.phrase, m.before, m.after), ("status code", None, None))

    def test_context_inside_outer_backtick_form(self):
        (m,) = parse_markers('Y `grep: "foo" after "baz"`.')
        self.assertEqual((m.phrase, m.after), ("foo", "baz"))

    def test_grep_markers_phrase_only_view_unchanged(self):
        self.assertEqual(grep_markers('a (grep: `x`) b (grep-re: `y.*`)'), ["x", "y.*"])

    def test_multi_phrase_marker_captures_all(self):
        ms = parse_markers('(grep: `A`; `B` | `C`)')
        self.assertEqual([m.phrase for m in ms], ["A", "B", "C"])
        self.assertTrue(all(not m.regex for m in ms))                # extras inherit literal

    def test_multi_phrase_inherits_regex_type(self):
        ms = parse_markers('(grep-re: `a.*b`; `c.*d`)')
        self.assertEqual([(m.phrase, m.regex) for m in ms], [("a.*b", True), ("c.*d", True)])

    def test_multi_phrase_across_outer_backtick_form(self):
        ms = parse_markers('(`grep: "A"`; `B`)')
        self.assertEqual([m.phrase for m in ms], ["A", "B"])

    def test_re_keyed_second_anchor_is_not_swallowed_as_bare(self):
        # `(`grep: "A"`; `grep: "B"`)` — each phrase re-states the key; both are full anchors,
        # captured cleanly (NOT as the literal phrase `grep: "B"`).
        ms = parse_markers('(`grep: "A"`; `grep: "B"`)')
        self.assertEqual([m.phrase for m in ms], ["A", "B"])

    def test_semicolon_after_close_paren_is_not_an_extra(self):
        # a `)` closing the marker (or a stray prose `;`) must not pull following text in
        ms = parse_markers('see (grep: `A`); then a bare `B` span')
        self.assertEqual([m.phrase for m in ms], ["A"])


# ---------------------------------------------------------------------------
class MatcherTests(unittest.TestCase):
    """P0 — literal by default (no regex fail-open); P1 — occurrence pinning."""

    def test_literal_by_default_no_regex_fallback(self):
        hay = haystacks("the cat sat on the mat")
        self.assertFalse(occurs("the.*sat", hay))               # literal: the metachars are absent
        self.assertTrue(occurs("the.*sat", hay, regex=True))    # opt-in regex: matches
        self.assertFalse(resolve(Marker("the.*sat"), hay).found)
        self.assertTrue(resolve(Marker("the.*sat", regex=True), hay).found)

    def test_broken_regex_marker_does_not_crash(self):
        self.assertFalse(resolve(Marker("(unclosed", regex=True), haystacks("text")).found)

    def test_occurrence_index_and_ambiguity(self):
        hay = haystacks("foo bar foo baz foo qux")
        self.assertTrue(resolve(Marker("foo"), hay).ambiguous)   # bare, 3× → ambiguous
        self.assertFalse(resolve(Marker("foo", nth=3), hay).ambiguous)
        self.assertTrue(resolve(Marker("foo", nth=3), hay).found)
        self.assertFalse(resolve(Marker("foo", nth=4), hay).found)

    def test_prefix_suffix_pins_occurrence(self):
        hay = haystacks("foo bar foo baz")
        self.assertTrue(resolve(Marker("foo", after="baz"), hay).found)    # the 2nd foo
        self.assertTrue(resolve(Marker("foo", before="bar"), hay).found)   # the foo after 'bar'
        self.assertFalse(resolve(Marker("foo", after="qux"), hay).found)   # no foo precedes 'qux'

    def test_context_survives_line_wrap(self):
        # a hyphen-wrapped source still resolves with context, in the de-hyphenated space
        hay = haystacks("the co-\noperation of many")
        self.assertTrue(resolve(Marker("cooperation", after="of"), hay).found)


# ---------------------------------------------------------------------------
class AnchorCheckTests(TempLibTest):
    """P0/P1 at the linter level: the fail-open is closed and pinning is honoured."""

    def _thin(self, body):
        build(self.cfg)
        set_thin(self.cfg, "foo", body)

    def test_literal_metachar_anchor_is_rot_not_fallback(self):
        self._thin('Bad anchor — `grep: "quick.*lazy"`.')      # not verbatim in the source
        r = check(self.cfg)
        self.assertFalse(r.ok)
        self.assertTrue(any("citation-rot" in e for e in r.errors), msg=r.errors)

    def test_regex_anchor_opt_in_resolves(self):
        self._thin('Intentional regex — `grep-re: "quick.*lazy"`.')
        self.assertTrue(check(self.cfg).ok, msg=check(self.cfg).errors)

    def test_strict_flags_ambiguous_anchor(self):
        build(self.cfg)
        (self.cfg.lib / "books" / "foo.txt").write_text(
            "alpha beta alpha gamma alpha delta", encoding="utf-8")
        set_thin(self.cfg, "foo", 'Ambiguous — `grep: "alpha"`.')
        self.assertTrue(check(self.cfg).ok)                     # default: resolves, silent
        r = check(self.cfg, strict=True)
        self.assertTrue(any("F-ambiguous" in w for w in r.warns), msg=r.warns)

    def test_pinned_occurrence_in_check(self):
        build(self.cfg)
        (self.cfg.lib / "books" / "foo.txt").write_text("alpha beta alpha gamma", encoding="utf-8")
        set_thin(self.cfg, "foo", 'Second — `grep: "alpha" #2`.')
        self.assertTrue(check(self.cfg).ok, msg=check(self.cfg).errors)
        set_thin(self.cfg, "foo", 'No third — `grep: "alpha" #3`.')
        self.assertFalse(check(self.cfg).ok)


# ---------------------------------------------------------------------------
class _FakeBackend:
    """A deterministic stand-in for the NLI model — the plumbing under test, not the model."""
    def __init__(self, support: float):
        self.name = "fake"
        self._s = support

    def support(self, premise: str, claim: str) -> float:
        return self._s


class EntailUnitTests(unittest.TestCase):
    """P2 — the pure, stdlib windowing/claim layer (no model needed)."""

    def test_window_is_sentence_granular(self):
        raw = "The fox ran off. The lazy dog slept here. Birds flew by."
        w = entail.window_around(raw, "lazy dog")
        self.assertIn("lazy dog slept", w)
        self.assertIn("The fox ran off", w)          # ± one neighbour sentence

    def test_claim_strips_marker_scaffolding(self):
        claim = entail.claim_for('The dog is lazy — `grep: "lazy dog"`. Next one.', "lazy dog")
        self.assertIn("dog is lazy", claim)
        self.assertNotIn("grep", claim)

    def test_claim_uses_marker_occurrence_not_earlier_prose(self):
        # the same words appear in prose first; the claim must come from the ANCHOR's sentence
        body = 'The lazy dog naps in the sun. A distinct claim carries it — `grep: "lazy dog"`.'
        claim = entail.claim_for(body, "lazy dog")
        self.assertIn("distinct claim", claim)
        self.assertNotIn("naps in the sun", claim)

    def test_score_card_skips_regex_anchors(self):
        card = ('## Thin\nA claim — `grep: "lazy dog"`; a spanned one — `grep-re: "fox.*ran"`.')
        raw = "The fox soon ran. The lazy dog slept."
        scores = entail.score_card(card, raw, _FakeBackend(0.9), "foo")
        self.assertEqual([s.phrase for s in scores], ["lazy dog"])   # regex anchor skipped
        self.assertEqual(scores[0].support, 0.9)

    def test_backend_absent_degrades(self):
        # the [entail] extra is not installed in the test env → clean, guided failure, not a crash
        with self.assertRaises(entail.EntailUnavailable):
            entail.load_backend()


class FreshnessTests(TempLibTest):
    """P3 — freshness (source drift / review lapse / supersession) warns, never gates."""

    def _add_fm_field(self, key, val):
        card = self.cfg.cards / "foo.md"
        card.write_text(card.read_text(encoding="utf-8").replace(
            "grep_ready: true", f"grep_ready: true\n{key}: {val}", 1), encoding="utf-8")

    def test_stamp_then_source_change_is_detected(self):
        build(self.cfg, stamp=True)
        self.assertIn("source_sha256:", (self.cfg.cards / "foo.md").read_text(encoding="utf-8"))
        self.assertTrue(check(self.cfg).ok)                          # stamped == live → clean
        (self.cfg.lib / "books" / "foo.txt").write_text("a wholly different source", encoding="utf-8")
        r = check(self.cfg)
        self.assertTrue(any("H source-changed" in w for w in r.warns), msg=r.warns)
        self.assertTrue(r.ok)                                        # freshness is WARN, not a gate

    def test_unstamped_card_carries_no_hash_and_no_warn(self):
        build(self.cfg)                                              # no --stamp
        self.assertNotIn("source_sha256:", (self.cfg.cards / "foo.md").read_text(encoding="utf-8"))
        (self.cfg.lib / "books" / "foo.txt").write_text("changed", encoding="utf-8")
        self.assertFalse(any("source-changed" in w for w in check(self.cfg).warns))

    def test_past_review_date_warns(self):
        build(self.cfg)
        self._add_fm_field("review_by", "2020-01-01")
        self.assertTrue(any("H stale" in w for w in check(self.cfg, today=date(2026, 7, 24)).warns))

    def test_future_review_date_is_silent(self):
        build(self.cfg)
        self._add_fm_field("review_by", "2099-01-01")
        self.assertFalse(any("H stale" in w for w in check(self.cfg, today=date(2026, 7, 24)).warns))

    def test_superseded_warns(self):
        build(self.cfg)
        self._add_fm_field("superseded_by", "foo-v2")
        self.assertTrue(any("H superseded" in w for w in check(self.cfg).warns))


class EntailCheckTests(TempLibTest):
    """P2 — entailment is opt-in and WARN-ONLY: it never turns an ok library into a failure."""

    def setUp(self):
        super().setUp()
        build(self.cfg)
        set_thin(self.cfg, "foo", 'The fox clears it — `grep: "jumps over the lazy dog"`.')

    def test_low_support_warns_but_does_not_fail(self):
        r = check(self.cfg, entail=_FakeBackend(0.10), entail_threshold=0.5)
        self.assertTrue(r.ok)                                    # grep still gates; entail cannot fail
        self.assertTrue(any(w.startswith("[entail") for w in r.warns), msg=r.warns)
        self.assertTrue(any("advisory" in n for n in r.notes))

    def test_high_support_is_silent(self):
        r = check(self.cfg, entail=_FakeBackend(0.95), entail_threshold=0.5)
        self.assertTrue(r.ok)
        self.assertFalse(any(w.startswith("[entail") for w in r.warns), msg=r.warns)


class RankingTests(unittest.TestCase):
    """P4 — BM25 length normalization: a short, on-point card beats a long, diluted one that
    merely repeats the term (which the old raw `sum(count)` ranked backwards)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lodlib-rank-"))
        (self.tmp / "library.toml").write_text(
            '[library]\ndir = "library"\ncards = "cards"\nshelves = ["books"]\n'
            "[bibliography]\nenabled = false\n[copyright]\nguard = false\n", encoding="utf-8")
        lib = self.tmp / "library"
        (lib / "books").mkdir(parents=True)
        (lib / "books" / "short.txt").write_text("x", encoding="utf-8")
        (lib / "books" / "long.txt").write_text("x", encoding="utf-8")
        self.cfg = Config.load(self.tmp / "library.toml")
        build(self.cfg)
        set_thin(self.cfg, "short", "Mimesis is the core idea of this card.")
        set_thin(self.cfg, "long", "Mimesis recurs, mimesis again. " + "unrelated filler words here. " * 60)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_short_on_point_card_outranks_long_diluted_one(self):
        from lode.lib.query import search
        hits, _ = search(self.cfg, ["mimesis"])
        self.assertEqual([h.id for h in hits][0], "short", msg=[(h.id, round(h.score, 3)) for h in hits])


# ---------------------------------------------------------------------------
# Round-2 audit regressions — each fails on the pre-fix code.
class Round2MatcherTests(unittest.TestCase):
    def test_front_matter_tolerates_crlf_and_eof(self):
        self.assertEqual(front_matter("---\r\nid: foo\r\n---\r\n"), "id: foo")   # Windows CRLF
        self.assertEqual(front_matter("---\nid: foo\n---"), "id: foo")           # closing --- at EOF

    def test_regex_anchor_honours_occurrence_pin(self):
        hay = haystacks("alpha foo alpha foo alpha")           # 'f.o' matches 'foo' twice
        self.assertTrue(resolve(Marker("f.o", regex=True, nth=2), hay).found)
        self.assertFalse(resolve(Marker("f.o", regex=True, nth=3), hay).found)   # was a fail-open

    def test_line_wrapped_context_word_still_resolves(self):
        # A#3: the context word 'information' is hyphen-wrapped in the source — a genuine quote
        # must NOT be flagged as rot.
        hay = haystacks("driven by informa-\ntion catharsis emerges")
        self.assertTrue(resolve(Marker("catharsis", before="information"), hay).found)


class Round2ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lodlib-cfg2-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _load(self, toml):
        (self.tmp / "library.toml").write_text(toml, encoding="utf-8")
        return Config.load(self.tmp / "library.toml")

    def test_shelves_as_bare_string_rejected(self):
        with self.assertRaises(ConfigError):                 # was iterated into ('b','o','o','k','s')
            self._load('[library]\ndir = "library"\nshelves = "books"\n')

    def test_backup_keep_non_int_is_configerror(self):
        with self.assertRaises(ConfigError):                 # was a raw ValueError traceback
            self._load('[library]\ndir = "library"\nshelves = ["books"]\n'
                       '[normalize]\nbackup_keep = "three"\n')

    def test_extra_guard_traversal_is_configerror(self):
        with self.assertRaises(ConfigError):                 # was a raw ValueError from relative_to
            self._load('[library]\ndir = "library"\nshelves = ["books"]\n'
                       '[copyright]\nextra_guard_dirs = ["../../etc"]\n')


class Round2BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lodlib-b2-"))
        (self.tmp / "library.toml").write_text(
            '[library]\ndir = "library"\ncards = "cards"\nshelves = ["books"]\n'
            '[bibliography]\nenabled = true\npath = "BIBLIOGRAPHY.md"\n[copyright]\nguard = false\n',
            encoding="utf-8")
        lib = self.tmp / "library"
        (lib / "books").mkdir(parents=True)
        (lib / "books" / "plato.txt").write_text("apology", encoding="utf-8")
        (lib / "books" / "plato-republic.txt").write_text("republic", encoding="utf-8")
        # the Republic row is listed FIRST — the ordering that made the prefix-collision bug bite
        (lib / "BIBLIOGRAPHY.md").write_text(
            "| Plato — The Republic | `plato-republic.txt` | r |\n"
            "| Plato — Apology | `plato.txt` | a |\n", encoding="utf-8")
        self.cfg = Config.load(self.tmp / "library.toml")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_bibliography_prefix_collision(self):
        build(self.cfg)
        plato = (self.cfg.cards / "plato.md").read_text(encoding="utf-8")
        self.assertIn("Apology", plato)          # its OWN row
        self.assertNotIn("Republic", plato)      # not the longer stem's row

    def test_fresh_clone_build_leaves_index_and_cards(self):
        build(self.cfg)
        idx_before = (self.cfg.cards / "INDEX.md").read_text(encoding="utf-8")
        (self.cfg.lib / "books" / "plato.txt").unlink()          # simulate a fresh clone
        (self.cfg.lib / "books" / "plato-republic.txt").unlink()
        st = build(self.cfg)
        self.assertIn("skipped", st)
        self.assertEqual((self.cfg.cards / "INDEX.md").read_text(encoding="utf-8"), idx_before)
        self.assertTrue((self.cfg.cards / "plato.md").exists())  # cards not stubbed/deleted


class Round2BodyTests(TempLibTest):
    def test_rebuild_preserves_code_fence_blank_lines(self):
        build(self.cfg)
        path = self.cfg.cards / "foo.md"
        head = path.read_text(encoding="utf-8")[: path.read_text(encoding="utf-8").index("## Thin")]
        path.write_text(head + "## Thin\n\n```\nline1\n\n\nline2\n```\n\n## Full\n_(owed)_\n",
                        encoding="utf-8")
        before = path.read_text(encoding="utf-8")
        build(self.cfg)
        after = path.read_text(encoding="utf-8")
        self.assertEqual(before, after)                 # idempotent no-op on the hand-written body
        self.assertIn("\n\n\nline2", after)             # the fence's blank lines survived


class Round2IngestTests(TempLibTest):
    def test_ingest_rejects_traversing_id(self):
        from lode.lib.ingest import ingest
        (self.tmp / "d.pdf").write_bytes(b"%PDF-1.4 dummy")
        with self.assertRaises(ValueError):              # was written outside the library tree
            ingest(self.cfg, self.tmp / "d.pdf", "books", card_id="../../../tmp/escape")


class ConsoleTests(unittest.TestCase):
    """`lib consult` / `lib diagnose` — the writer's craft console over the frameworks layer."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lodlib-console-"))
        (self.tmp / "library.toml").write_text(
            '[library]\ndir = "library"\ncards = "cards"\nshelves = ["books"]\n'
            "[frameworks]\nenabled = true\n[bibliography]\nenabled = false\n[copyright]\nguard = false\n",
            encoding="utf-8")
        lib = self.tmp / "library"
        (lib / "books").mkdir(parents=True)
        (lib / "cards").mkdir()
        syn = lib / "frameworks" / "_syntheses"
        syn.mkdir(parents=True)
        (lib / "frameworks" / "tester.md").write_text(
            "# Framework — A Tester\n\n**Dimension:** Testcraft\n**Aliases:** probing, checking\n\n"
            "### 1. Engine\nthe core drive.\n### 4. Practices (what to DO)\ndo the thing.\n"
            "### 7. Disagreement\nargues with others.\n", encoding="utf-8")
        (syn / "testcraft.md").write_text(
            "---\ntitle: Synthesis — Testcraft\nstatus: canonical\ndimension: testcraft\ncards: [tester]\n---\n\n"
            "# testcraft\n\n**Core question:** how do you test the craft?\n\n"
            "## Craft\nthe writer move — mark the gap.\n\n"
            "## The options\noption A vs B.\n\n## Operational spec\ndo X then Y.\n", encoding="utf-8")
        (syn / "_diagnostics.md").write_text(
            "# Diag\n\n| symptom cues | dimensions |\n|---|---|\n"
            "| drags, boring | testcraft |\n", encoding="utf-8")
        self.cfg_path = self.tmp / "library.toml"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["-c", str(self.cfg_path), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_consult_lists_lenses(self):
        code, out, _ = self._run("consult")
        self.assertEqual(code, 0)
        self.assertIn("testcraft", out)                     # the dimension
        self.assertIn("tester", out)                        # the thinker
        self.assertNotIn("_diagnostics", out)               # data file is not a lens

    def test_consult_dimension_section_narrows(self):
        code, out, _ = self._run("consult", "testcraft", "--section", "spec")
        self.assertEqual(code, 0)
        self.assertIn("do X then Y", out)                   # the operational-spec section
        self.assertNotIn("option A vs B", out)              # other sections excluded

    def test_consult_dimension_default_is_craft(self):
        code, out, _ = self._run("consult", "testcraft")
        self.assertEqual(code, 0)
        self.assertIn("how do you test the craft", out)     # core question in the header
        self.assertIn("the writer move", out)               # the Craft layer, by default
        self.assertNotIn("do X then Y", out)                # engine spec hidden by default

    def test_consult_dimension_full_shows_engine(self):
        code, out, _ = self._run("consult", "testcraft", "--full")
        self.assertEqual(code, 0)
        self.assertIn("option A vs B", out)
        self.assertIn("do X then Y", out)                   # whole document

    def test_consult_dimension_menu_when_no_craft(self):
        (self.tmp / "library" / "frameworks" / "_syntheses" / "testcraft.md").write_text(
            "---\ntitle: T\nstatus: canonical\ndimension: testcraft\ncards: []\n---\n\n"
            "# testcraft\n\n**Core question:** q?\n\n## The options\nA.\n\n## Operational spec\nB.\n",
            encoding="utf-8")
        code, out, _ = self._run("consult", "testcraft")   # no Craft → falls back to the menu
        self.assertEqual(code, 0)
        self.assertIn("operational spec", out)

    def test_consult_framework_default_view(self):
        code, out, _ = self._run("consult", "tester")       # not a dimension → resolves as framework
        self.assertEqual(code, 0)
        self.assertIn("the core drive", out)                # engine section shown by default

    def test_consult_unknown_lens_errors(self):
        code, _, err = self._run("consult", "nope")
        self.assertEqual(code, 1)
        self.assertIn("no lens", err)

    def test_diagnose_routes_symptom_to_dimension(self):
        code, out, _ = self._run("diagnose", "the scene really drags here")
        self.assertEqual(code, 0)
        self.assertIn("testcraft", out)

    def test_diagnose_no_match_exits_nonzero(self):
        code, _, err = self._run("diagnose", "zxqw utterly unrelated")
        self.assertEqual(code, 1)

    def _set_diag(self, content):
        (self.tmp / "library" / "frameworks" / "_syntheses" / "_diagnostics.md").write_text(
            content, encoding="utf-8")

    def test_diagnose_word_boundary_not_substring(self):
        # a short cue must not fire inside an unrelated word ('pace' in 'spacecraft')
        self._set_diag("# D\n\n| symptom cues | dimensions |\n|---|---|\n| pace | testcraft |\n")
        self.assertEqual(self._run("diagnose", "the spacecraft landed")[0], 1)   # no false match
        code, out, _ = self._run("diagnose", "the pace is slow")                 # 'pace' as a word
        self.assertEqual(code, 0)
        self.assertIn("testcraft", out)

    def test_diagnose_skips_nonstandard_header(self):
        # a header with any label (not just "symptom cues") must not become a routing row
        self._set_diag("# D\n\n| feeling | panels |\n|---|---|\n| drags | testcraft |\n")
        code, out, _ = self._run("diagnose", "the feeling is off")
        self.assertEqual(code, 1)
        self.assertNotIn("panels", out)

    def test_consult_framework_section_substring(self):
        code, out, _ = self._run("consult", "tester", "--section", "practice")   # substring → practices
        self.assertEqual(code, 0)
        self.assertIn("do the thing", out)

    def test_consult_dimension_section_all(self):
        code, out, _ = self._run("consult", "testcraft", "--section", "all")
        self.assertEqual(code, 0)
        self.assertIn("option A vs B", out)
        self.assertIn("do X then Y", out)

    def test_consult_rejects_path_traversal(self):
        code, _, err = self._run("consult", "../../etc/passwd")
        self.assertEqual(code, 1)
        self.assertIn("no lens", err)


class ConsultByNameTests(unittest.TestCase):
    """`lib consult` resolves an author name or a book title, not just a stem."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lodlib-byname-"))
        (self.tmp / "library.toml").write_text(
            '[library]\ndir = "library"\ncards = "cards"\nshelves = ["books"]\n'
            "[frameworks]\nenabled = true\n[bibliography]\nenabled = false\n[copyright]\nguard = false\n",
            encoding="utf-8")
        lib = self.tmp / "library"
        (lib / "books").mkdir(parents=True)
        cards = lib / "cards"
        cards.mkdir()
        syn = lib / "frameworks" / "_syntheses"
        syn.mkdir(parents=True)
        # a framework'd book: the framework card references its source, so it represents the book
        (lib / "frameworks" / "author.md").write_text(
            "# Framework — Jane Author, *The Big Book* (2001)\n\n"
            "**Dimension:** Testcraft · **Source:** `library/books/author-big-book.txt`\n"
            "**Aliases:** jane author, doing the thing\n\n### 1. Engine\nthe author's core idea.\n",
            encoding="utf-8")
        (cards / "author-big-book.md").write_text(
            "---\nid: author-big-book\nfile: library/books/author-big-book.txt\naliases: [jane author]\n---\n"
            "# Jane Author — The Big Book\n\n## Thin\nbig-book gist.\n", encoding="utf-8")
        # a source-only book: no framework
        (cards / "writer-small-book.md").write_text(
            "---\nid: writer-small-book\nfile: library/books/writer-small-book.txt\n"
            "aliases: [ben writer, tiny tome]\n---\n"
            "# Ben Writer — The Small Book\n\n## Thin\nsmall-book gist.\n\n## Full\nmore small detail.\n",
            encoding="utf-8")
        self.cfg_path = self.tmp / "library.toml"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["-c", str(self.cfg_path), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_author_name_resolves_to_framework(self):
        code, out, _ = self._run("consult", "jane author")
        self.assertEqual(code, 0)
        self.assertIn("the author's core idea", out)             # the framework engine

    def test_book_title_resolves_to_framework(self):
        code, out, _ = self._run("consult", "the big book")
        self.assertEqual(code, 0)
        self.assertIn("the author's core idea", out)

    def test_source_only_book_resolves_to_source_card(self):
        code, out, _ = self._run("consult", "ben writer")        # no framework → the source card
        self.assertEqual(code, 0)
        self.assertIn("small-book gist", out)

    def test_source_only_by_title(self):
        code, out, _ = self._run("consult", "the small book")
        self.assertEqual(code, 0)
        self.assertIn("small-book gist", out)

    def test_ambiguous_lists_candidates(self):
        code, _, err = self._run("consult", "book")              # 'book' in both
        self.assertEqual(code, 1)
        self.assertIn("author", err)
        self.assertIn("writer-small-book", err)

    def test_exact_stem_still_resolves(self):
        code, out, _ = self._run("consult", "author")            # exact framework stem
        self.assertEqual(code, 0)
        self.assertIn("core idea", out)


class McpAudienceTests(unittest.TestCase):
    """MCP `consult_dimension` audience projection: writer / engine / full + section override."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lodlib-mcpaud-"))
        (self.tmp / "library.toml").write_text(
            '[library]\ndir = "library"\ncards = "cards"\nshelves = ["books"]\n'
            "[frameworks]\nenabled = true\n[bibliography]\nenabled = false\n[copyright]\nguard = false\n",
            encoding="utf-8")
        lib = self.tmp / "library"
        (lib / "books").mkdir(parents=True)
        (lib / "cards").mkdir()
        self.syn = lib / "frameworks" / "_syntheses"
        self.syn.mkdir(parents=True)
        self._write("## Craft\nthe writer move.\n\n"
                    "## Operational spec for the engine\nscore it thus.\n\n## Owed\nprovenance here.\n")

    def _write(self, body):
        (self.syn / "testcraft.md").write_text(
            "---\ntitle: T\nstatus: canonical\ndimension: testcraft\ncards: []\n---\n\n"
            "# testcraft\n\n**Core question:** q?\n\n" + body, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _dim(self, **args):
        from lode.lib import mcp_server
        return mcp_server._tool_consult_dimension(Config.load(self.tmp / "library.toml"),
                                                  {"dimension": "testcraft", **args})

    def test_writer_default_is_craft_only(self):
        out = self._dim()
        self.assertIn("the writer move", out)
        self.assertNotIn("score it thus", out)          # engine hidden
        self.assertNotIn("provenance here", out)         # provenance hidden

    def test_engine_adds_the_mapping(self):
        out = self._dim(audience="engine")
        self.assertIn("the writer move", out)            # craft
        self.assertIn("score it thus", out)              # + engine mapping
        self.assertNotIn("provenance here", out)         # but not audit

    def test_full_shows_everything(self):
        out = self._dim(audience="full")
        self.assertIn("score it thus", out)
        self.assertIn("provenance here", out)

    def test_section_overrides_audience(self):
        out = self._dim(section="owed")
        self.assertIn("provenance here", out)
        self.assertNotIn("the writer move", out)

    def test_engine_with_no_engine_section_notes(self):
        self._write("## Craft\nthe writer move.\n\n## Owed\nprov.\n")   # no engine section
        out = self._dim(audience="engine")
        self.assertIn("no engine-mapping section", out)


class McpResolveTests(unittest.TestCase):
    """MCP parity: consult_framework/consult_dimension resolve an author name or book title."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lodlib-mcpres-"))
        (self.tmp / "library.toml").write_text(
            '[library]\ndir = "library"\ncards = "cards"\nshelves = ["books"]\n'
            "[frameworks]\nenabled = true\n[bibliography]\nenabled = false\n[copyright]\nguard = false\n",
            encoding="utf-8")
        lib = self.tmp / "library"
        (lib / "books").mkdir(parents=True)
        cards = lib / "cards"
        cards.mkdir()
        syn = lib / "frameworks" / "_syntheses"
        syn.mkdir(parents=True)
        (lib / "frameworks" / "author.md").write_text(
            "# Framework — Jane Author, *The Big Book*\n\n"
            "**Dimension:** Testcraft · **Source:** `library/books/author-big-book.txt`\n"
            "**Aliases:** jane author\n\n### 1. Engine\nthe author's core idea.\n", encoding="utf-8")
        (cards / "author-big-book.md").write_text(
            "---\nid: author-big-book\nfile: library/books/author-big-book.txt\naliases: [jane author]\n---\n"
            "# Jane Author — The Big Book\n\n## Thin\nbig-book gist.\n", encoding="utf-8")
        (cards / "writer-small-book.md").write_text(
            "---\nid: writer-small-book\nfile: library/books/writer-small-book.txt\naliases: [ben writer]\n---\n"
            "# Ben Writer — The Small Book\n\n## Thin\nsmall-book gist.\n", encoding="utf-8")
        (cards / "quiet-manuscript.md").write_text(   # source-only, un-distilled (owed placeholders)
            "---\nid: quiet-manuscript\nfile: library/books/quiet-manuscript.txt\naliases: [quiet author]\n---\n"
            "# Quiet Author — Manuscript\n\n## Thin\n_(L1 owed — 1-3 sentences, grep-grounded to the .txt)_\n"
            "\n## Full\n_(L2 owed — main points outlined, grep-cited)_\n", encoding="utf-8")
        (syn / "mydim.md").write_text(
            "---\ntitle: T\nstatus: canonical\ndimension: mydim\ncards: []\n---\n"
            "# mydim\n\n**Core question:** q?\n\n## Craft\nthe move.\n", encoding="utf-8")
        self.cfg = Config.load(self.tmp / "library.toml")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fw(self, name):
        from lode.lib import mcp_server
        return mcp_server._tool_consult_framework(self.cfg, {"name": name})

    def _dim(self, name):
        from lode.lib import mcp_server
        return mcp_server._tool_consult_dimension(self.cfg, {"dimension": name})

    def test_framework_by_author_name(self):
        self.assertIn("core idea", self._fw("jane author"))

    def test_framework_by_book_title(self):
        self.assertIn("core idea", self._fw("the big book"))

    def test_source_only_book_returns_summary(self):
        out = self._fw("ben writer")                       # no framework → its source card
        self.assertIn("small-book gist", out)
        self.assertIn("source card", out)

    def test_framework_tool_redirects_a_dimension(self):
        self.assertIn("consult_dimension", self._fw("mydim"))

    def test_dimension_tool_redirects_a_thinker(self):
        self.assertIn("consult_framework", self._dim("jane author"))

    def test_dimension_tool_redirects_a_source_book(self):
        self.assertIn("consult_framework", self._dim("ben writer"))   # source → framework tool

    def test_ambiguous_name_lists_candidates(self):
        self.assertIn("matches several lenses", self._fw("book"))     # both books contain 'book' (shared note)

    def test_empty_name_is_no_lens(self):
        self.assertIn("no lens", self._fw(""))

    def test_owed_placeholder_hidden_in_source_summary(self):
        out = self._fw("quiet author")
        self.assertIn("no L1/L2 written yet", out)
        self.assertNotIn("owed", out)                                # the build stub is not surfaced

    def test_no_lens(self):
        self.assertIn("no lens", self._fw("zzzznope"))


class ResolutionParityTests(unittest.TestCase):
    """consult resolution + CLI/MCP parity fixes — query.resolve / query.diagnose."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lodlib-parity-"))
        (self.tmp / "library.toml").write_text(
            '[library]\ndir = "library"\ncards = "cards"\nshelves = ["books"]\n'
            "[frameworks]\nenabled = true\n[bibliography]\nenabled = false\n[copyright]\nguard = false\n",
            encoding="utf-8")
        lib = self.tmp / "library"
        (lib / "books").mkdir(parents=True)
        (lib / "cards").mkdir()
        self.syn = lib / "frameworks" / "_syntheses"
        self.syn.mkdir(parents=True)
        for m in ("view-who", "view-showing"):            # a dimension FAMILY sharing the `view-` prefix
            (self.syn / f"{m}.md").write_text(
                f"---\ntitle: T\nstatus: canonical\ndimension: {m}\ncards: []\n---\n"
                f"# {m}\n\n**Core question:** who tells it?\n\n## Craft\nthe move.\n", encoding="utf-8")
        # a framework whose alias INCIDENTALLY contains the family word 'view' — the §1 trap
        (lib / "frameworks" / "stockish.md").write_text(
            "# Framework — A Stylist\n\n**Dimension:** Prose / style\n"
            "**Aliases:** texture, cognitive view, resonance\n\n### 1. Engine\nprose texture.\n", encoding="utf-8")
        (self.syn / "_diagnostics.md").write_text(        # an ASCII cue and a CJK cue, same dimension
            "# D\n\n| symptom cues | dimensions |\n|---|---|\n| drags, 信息倾倒 | view-who |\n", encoding="utf-8")
        self.cfg = Config.load(self.tmp / "library.toml")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # §1 — a family query reaches the dimensions, never an incidental framework alias
    def test_family_query_is_ambiguous_over_its_dimensions(self):
        r = query.resolve(self.cfg, "view")
        self.assertEqual(r.outcome, "ambiguous")
        self.assertEqual({c.name for c in r.candidates}, {"view-who", "view-showing"})

    def test_family_query_never_wins_an_incidental_framework(self):
        r = query.resolve(self.cfg, "view")
        self.assertNotEqual(r.outcome, "framework")
        self.assertFalse(any(c.name == "stockish" for c in r.candidates))

    def test_single_family_member_resolves_to_that_dimension(self):
        (self.syn / "view-showing.md").unlink()           # only one family member remains
        self.cfg = Config.load(self.tmp / "library.toml")
        r = query.resolve(self.cfg, "view")
        self.assertEqual(r.outcome, "dimension")
        self.assertEqual(r.match.name, "view-who")

    # §6 — one canonical note, read by both frontends
    def test_none_note_says_no_lens(self):
        r = query.resolve(self.cfg, "zzzznope")
        self.assertEqual(r.outcome, "none")
        self.assertIn("no lens", r.note)

    def test_ambiguous_note_lists_candidate_names(self):
        note = query.resolve(self.cfg, "view").note
        self.assertIn("view-who", note)
        self.assertIn("view-showing", note)

    # §4 — a CJK cue fires inside a real CJK sentence (dead under the old \\b matcher)
    def test_cjk_cue_fires_inside_a_sentence(self):
        result = query.diagnose(self.cfg, "这里信息倾倒了")
        self.assertIsNotNone(result)
        self.assertIn("view-who", [dn for dn, _ in result])

    def test_ascii_cue_keeps_word_boundary(self):
        self.assertEqual(query.diagnose(self.cfg, "the raggeddrags thing"), [])   # no boundary → no fire
        self.assertIn("view-who", [dn for dn, _ in query.diagnose(self.cfg, "it drags")])

    # §3 — MCP can now route a symptom via the shared logic
    def test_mcp_diagnose_tool_routes_symptom(self):
        self.assertIn("diagnose", mcp_server.DISPATCH)
        out = mcp_server._tool_diagnose(self.cfg, {"symptom": "this really drags"})
        self.assertIn("view-who", out)


if __name__ == "__main__":
    unittest.main()
