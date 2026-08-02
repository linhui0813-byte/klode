"""Gate hardening (Loop-B safety) — the two defects a supervising gate must not have:

  1. Fail-OPEN partial grounding: dropping an ungrounded criterion and renormalizing the average
     can turn Recycle into Go. A gate that gets *safer* when it loses evidence is inverted.
  2. Freshness bypass: grounding through the occurrence-only `verify` lets a STALE or review-EXPIRED
     source ground a criterion.

The fix is a fail-CLOSED contract: any applicable criterion without current, unambiguous evidence
yields a verdict of "Unavailable" — never Go/Recycle. Proven here against temp KBs built per-test,
with an injected `today` so the review-date logic is deterministic (no wall-clock dependency).
"""
import hashlib
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from klode import lib                                                     # noqa: E402
from klode.gate import FixtureJudge, ground, load_criteria, review_draft  # noqa: E402

R = lib.EvidenceResolution


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_kb(root: Path, *, moves, source_text, stamp=False, review_by=None,
             dim="craft", cid="src1") -> Path:
    """Scaffold a minimal, self-contained KB: one source, one card, one Craft dimension whose
    bullets cite `moves` = [(move_name, [phrase, ...]), ...]. Returns the library.toml path."""
    lib_dir = root / "library"
    (lib_dir / "books").mkdir(parents=True, exist_ok=True)
    (lib_dir / "cards").mkdir(parents=True, exist_ok=True)
    (lib_dir / "frameworks" / "_syntheses").mkdir(parents=True, exist_ok=True)

    (root / "library.toml").write_text(
        '[library]\ndir = "library"\ncards = "cards"\nshelves = ["books"]\n'
        '[bibliography]\nenabled = false\n'
        '[frameworks]\nenabled = true\ndir = "frameworks"\nsyntheses = "_syntheses"\n',
        encoding="utf-8")

    (lib_dir / "books" / f"{cid}.txt").write_text(source_text, encoding="utf-8")

    fm = [f"id: {cid}", "shelf: books", f"file: library/books/{cid}.txt",
          "framework: none", "zoom: full", "aliases: []", "grep_ready: true"]
    if stamp:
        fm.append(f"source_sha256: {_sha(source_text)}")
    if review_by is not None:
        fm.append(f"review_by: {review_by}")
    (lib_dir / "cards" / f"{cid}.md").write_text(
        "---\n" + "\n".join(fm) + "\n---\n\n# Source\n\n## Content\n"
        f"`library/books/{cid}.txt` — grep it to verify.\n", encoding="utf-8")
    (lib_dir / "cards" / "INDEX.md").write_text(f"# Card Index\n\n- [{cid}]({cid}.md)\n", encoding="utf-8")

    def _bullet(m):
        name, phrases = m[0], m[1]
        prose = (m[2] + " ") if len(m) > 2 else ""       # optional guidance prose (3-tuple)
        return f"- **{name}.** {prose}" + " ".join(f"(grep: `{p}`)" for p in phrases)
    bullets = "\n".join(_bullet(m) for m in moves)
    (lib_dir / "frameworks" / "_syntheses" / f"{dim}.md").write_text(
        f"---\ntitle: Synthesis — {dim}\nstatus: canonical\ndimension: {dim}\ncards: [{cid}]\n---\n\n"
        f"# Synthesis — {dim}\n\n**Core question:** test?\n\n## Craft\n\nintro.\n\n{bullets}\n",
        encoding="utf-8")
    return root / "library.toml"


class GateHardening(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-gate-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cfg(self, **kw):
        return lib.Config.load(_make_kb(self.tmp, **kw))

    # --- the structured verifier: explicit outcomes, freshness- and review-aware ---
    def test_found_grounds(self):
        cfg = self._cfg(moves=[("M", ["the quick brown fox"])], source_text="the quick brown fox jumps")
        ev = lib.verify_evidence(cfg, "src1", "the quick brown fox")
        self.assertEqual(ev.resolution, R.FOUND)

    def test_not_found(self):
        cfg = self._cfg(moves=[("M", ["absent"])], source_text="the quick brown fox")
        self.assertEqual(lib.verify_evidence(cfg, "src1", "no such phrase here").resolution, R.NOT_FOUND)

    def test_ambiguous_does_not_ground(self):
        # a phrase occurring in more than one place is not usable as required evidence
        cfg = self._cfg(moves=[("M", ["repeat me"])], source_text="repeat me ... and repeat me again")
        ev = lib.verify_evidence(cfg, "src1", "repeat me")
        self.assertEqual(ev.resolution, R.AMBIGUOUS)

    def test_mutated_source_is_stale(self):
        cfg = self._cfg(moves=[("M", ["anchor phrase"])], source_text="anchor phrase here", stamp=True)
        # mutate the source AFTER stamping: current hash no longer matches the stored one
        (self.tmp / "library" / "books" / "src1.txt").write_text("anchor phrase here plus drift",
                                                                  encoding="utf-8")
        self.assertEqual(lib.verify_evidence(cfg, "src1", "anchor phrase").resolution, R.SOURCE_STALE)

    def test_unstamped_source_when_stamp_required(self):
        cfg = self._cfg(moves=[("M", ["anchor phrase"])], source_text="anchor phrase here")  # not stamped
        self.assertEqual(
            lib.verify_evidence(cfg, "src1", "anchor phrase", require_stamp=True).resolution,
            R.SOURCE_UNSTAMPED)
        # default (require_stamp=False) still grounds an unstamped-but-present phrase
        self.assertEqual(lib.verify_evidence(cfg, "src1", "anchor phrase").resolution, R.FOUND)

    def test_review_by_expired(self):
        cfg = self._cfg(moves=[("M", ["anchor phrase"])], source_text="anchor phrase here",
                        review_by="2020-01-01")
        ev = lib.verify_evidence(cfg, "src1", "anchor phrase", today=date(2026, 1, 1))
        self.assertEqual(ev.resolution, R.REVIEW_EXPIRED)

    def test_review_by_invalid(self):
        cfg = self._cfg(moves=[("M", ["anchor phrase"])], source_text="anchor phrase here",
                        review_by="not-a-date")
        self.assertEqual(lib.verify_evidence(cfg, "src1", "anchor phrase").resolution, R.REVIEW_DATE_INVALID)

    # --- ground() honors the structured verifier ---
    def test_ground_rejects_stale(self):
        cfg = self._cfg(moves=[("M", ["anchor phrase"])], source_text="anchor phrase here", stamp=True)
        (self.tmp / "library" / "books" / "src1.txt").write_text("drifted", encoding="utf-8")
        crit, panel = load_criteria(cfg, "craft")
        g = ground(cfg, crit[0], panel)
        self.assertFalse(g.grounded)
        self.assertEqual(g.resolution, R.SOURCE_STALE.value)

    # --- the fail-CLOSED invariant: no evidence failure may yield Go or Recycle ---
    def test_partial_grounding_yields_unavailable_not_go(self):
        # two criteria: one grounds, one cites an absent phrase. Even with a judge that would score
        # the grounded one 10/10, the verdict must be Unavailable — never Go, never Recycle.
        cfg = self._cfg(
            moves=[("Present move", ["anchor phrase"]), ("Absent move", ["nowhere in the source"])],
            source_text="anchor phrase here")
        v = review_draft(cfg, "a draft", "craft", FixtureJudge({}, default=(10, "great")))
        self.assertEqual(v.decision, "Unavailable")
        self.assertIsNone(v.score)
        self.assertTrue(any(cid == "C2" for cid, _ in v.unavailable))

    def test_no_evidence_failure_can_yield_a_verdict(self):
        # every failure mode routes to Unavailable, not Go/Recycle
        cases = [
            dict(moves=[("M", ["missing"])], source_text="present only"),                    # NOT_FOUND
            dict(moves=[("M", ["dup"])], source_text="dup and dup again"),                    # AMBIGUOUS
            dict(moves=[("M", ["p"])], source_text="p here", review_by="2000-01-01"),         # REVIEW_EXPIRED
        ]
        for kw in cases:
            with self.subTest(kw=kw):
                cfg = self._cfg(**kw)
                v = review_draft(cfg, "d", "craft", FixtureJudge({}, default=(10, "x")),
                                 today=date(2026, 1, 1))
                self.assertEqual(v.decision, "Unavailable")

    def test_all_grounded_still_scores_normally(self):
        # the fix must NOT break the happy path: all criteria grounded -> a real Go/Recycle
        cfg = self._cfg(moves=[("M", ["anchor phrase"])], source_text="anchor phrase here")
        go = review_draft(cfg, "d", "craft", FixtureJudge({}, default=(9, "strong")))
        self.assertEqual(go.decision, "Go")
        rec = review_draft(cfg, "d", "craft", FixtureJudge({}, default=(2, "weak")))
        self.assertEqual(rec.decision, "Recycle")


class EnrichedCriteria(unittest.TestCase):
    """The criterion carries the move's prose (guidance) and its criticality — no longer just the
    bold label + anchors. Closing the 'discarded prose' defect: a judge needs to know what a move
    MEANS, not only its headline."""
    FIX = REPO / "tests" / "fixtures" / "kb-fixture" / "library.toml"

    def test_guidance_is_captured_from_the_real_fixture(self):
        cfg = lib.Config.load(self.FIX)
        crit, _ = load_criteria(cfg, "pacing")
        self.assertEqual(len(crit), 3)                              # backward-compatible criterion count
        self.assertTrue(all(c.criticality == "required" for c in crit))
        cut = next(c for c in crit if c.statement.startswith("Cut what the reader"))
        self.assertIn("context", cut.guidance.lower())             # the explanation is retained
        self.assertNotIn("grep", cut.guidance)                     # anchor markers stripped from guidance
        self.assertNotIn("Trim every clause", cut.guidance)        # the anchor phrase itself is not prose

    def test_criticality_defaults_required_and_reads_advisory_tag(self):
        tmp = Path(tempfile.mkdtemp(prefix="klode-crit-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        cfg = lib.Config.load(_make_kb(
            tmp, source_text="alpha here and beta here",
            moves=[("Required move", ["alpha here"], "core rule"),
                   ("Advisory move", ["beta here"], "nice to have [advisory]")]))
        crit, _ = load_criteria(cfg, "craft")
        by = {c.statement: c for c in crit}
        self.assertEqual(by["Required move"].criticality, "required")
        self.assertEqual(by["Advisory move"].criticality, "advisory")
        self.assertEqual(by["Required move"].guidance, "core rule")

    def test_unanchored_move_is_a_hard_error_not_silently_dropped(self):
        # a stated move with no anchor must fail loud — silently dropping it would let the gate score
        # a reduced rubric and return Go (the parser-level fail-open)
        tmp = Path(tempfile.mkdtemp(prefix="klode-crit-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        cfg = lib.Config.load(_make_kb(
            tmp, source_text="anchored phrase here",
            moves=[("Anchored move", ["anchored phrase here"]), ("Naked move", [])]))
        with self.assertRaises(ValueError):
            load_criteria(cfg, "craft")

    def test_paren_inside_anchor_does_not_corrupt_guidance(self):
        tmp = Path(tempfile.mkdtemp(prefix="klode-crit-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        cfg = lib.Config.load(_make_kb(
            tmp, source_text="use foo (bar) baz somewhere",
            moves=[("M", ["use foo (bar) baz"], "the human note")]))
        c = load_criteria(cfg, "craft")[0][0]
        self.assertEqual(c.guidance, "the human note")               # the `)` in the anchor didn't leak
        self.assertNotIn("baz", c.guidance)

    def test_advisory_inside_an_anchor_does_not_flip_criticality(self):
        tmp = Path(tempfile.mkdtemp(prefix="klode-crit-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        cfg = lib.Config.load(_make_kb(
            tmp, source_text="the word [advisory] appears here",
            moves=[("M", ["the word [advisory] appears here"], "plain guidance")]))
        c = load_criteria(cfg, "craft")[0][0]
        self.assertEqual(c.criticality, "required")                  # tag inside the anchor is not a real tag


class EvidenceContextOp(unittest.TestCase):
    """verify_context returns the bounded surrounding source text a judge reads — not just the match
    line — and is `usable=False` for any non-grounding outcome. Closes the 'verify returns no span'
    gap, and is the automated version of validating that an anchor resolves to a real passage."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-ctx-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _cfg(self, **kw):
        return lib.Config.load(_make_kb(self.tmp, **kw))

    def test_context_window_includes_surrounding_lines(self):
        src = "line one\nline two\nTHE ANCHOR PHRASE here\nline four\nline five"
        cfg = self._cfg(moves=[("M", ["THE ANCHOR PHRASE"])], source_text=src)
        ctx = lib.verify_context(cfg, "src1", "THE ANCHOR PHRASE", context_lines=1)
        self.assertTrue(ctx.usable)
        self.assertEqual(ctx.match_lines, (3,))
        self.assertEqual((ctx.line_start, ctx.line_end), (2, 4))     # +/- 1 line around line 3
        self.assertIn("line two", ctx.text)
        self.assertIn("line four", ctx.text)
        self.assertNotIn("line one", ctx.text)                       # bounded — not the whole source

    def test_window_is_bounded_and_still_contains_the_match(self):
        src = "\n".join(["filler"] * 200 + ["needle here"] + ["filler"] * 200)   # needle on line 201
        cfg = self._cfg(moves=[("M", ["needle here"])], source_text=src)
        ctx = lib.verify_context(cfg, "src1", "needle here", context_lines=500, max_window=10)
        self.assertLessEqual(ctx.line_end - ctx.line_start + 1, 10)  # capped regardless of context_lines
        self.assertIn("needle here", ctx.text)                       # cap must NOT drop the match
        self.assertTrue(ctx.line_start <= 201 <= ctx.line_end)       # match line inside the window

    def test_bad_window_bounds_raise(self):
        cfg = self._cfg(moves=[("M", ["x here"])], source_text="x here")
        for bad in ({"max_window": 0}, {"max_window": -5}, {"context_lines": -1}, {"max_window": 1.5}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                lib.verify_context(cfg, "src1", "x here", **bad)

    def test_unlocatable_folded_is_not_usable(self):
        # a hyphenation fold resolves (folded-only) but the whitespace locator cannot place it
        cfg = self._cfg(moves=[("M", ["information hiding"])], source_text="pre\ninforma-\ntion hiding ok\npost")
        ctx = lib.verify_context(cfg, "src1", "information hiding")
        if ctx.resolution == R.FOLDED_ONLY and not ctx.match_lines:
            self.assertFalse(ctx.usable)                             # resolves, but no span -> not usable
            self.assertEqual(ctx.text, "")

    def test_folded_match_is_located(self):
        # "hello world" split across a line break resolves folded-only (no match line); still windowed
        cfg = self._cfg(moves=[("M", ["hello world"])], source_text="pre\nthe hello\nworld ok\npost")
        ctx = lib.verify_context(cfg, "src1", "hello world")
        self.assertEqual(ctx.resolution, R.FOLDED_ONLY)
        self.assertTrue(ctx.usable)
        self.assertIn("hello", ctx.text)
        self.assertIn("world", ctx.text)

    def test_failure_is_not_usable(self):
        cfg = self._cfg(moves=[("M", ["present"])], source_text="present here")
        ctx = lib.verify_context(cfg, "src1", "totally absent phrase")
        self.assertEqual(ctx.resolution, R.NOT_FOUND)
        self.assertFalse(ctx.usable)
        self.assertEqual(ctx.text, "")

    def test_stale_source_yields_no_span(self):
        cfg = self._cfg(moves=[("M", ["anchor here"])], source_text="anchor here", stamp=True)
        (self.tmp / "library" / "books" / "src1.txt").write_text("drifted", encoding="utf-8")
        ctx = lib.verify_context(cfg, "src1", "anchor here")
        self.assertEqual(ctx.resolution, R.SOURCE_STALE)
        self.assertFalse(ctx.usable)


if __name__ == "__main__":
    unittest.main()
