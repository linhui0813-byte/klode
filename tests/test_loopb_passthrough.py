"""WI-5 — Loop-B grounding-passthrough fixes.

The per-criterion, fail-closed gate already shipped; these pin the residual gaps: public defects
carry their source-verified (card, line); a multi-anchor criterion retains EVERY anchor's grounding;
an unknown judge criterion-id fails loud; and the judge receives each criterion's verified evidence
(a ContextBundle) — the seam the future real judge consumes. Grounding stays real; judge is faked.
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from klode import lib                                                     # noqa: E402
from klode.lib import services                                           # noqa: E402
from klode.lib.pool import KBPool                                        # noqa: E402
from klode.gate import FixtureJudge, Score, load_criteria, ground, review_draft   # noqa: E402

FIX = REPO / "tests" / "fixtures" / "kb-fixture" / "library.toml"        # dim "pacing", stamped cards
FIX2 = REPO / "tests" / "fixtures" / "kb-fixture-2" / "library.toml"     # dim "cadence", UNSTAMPED cards


class SpyJudge:
    """Captures what it was handed, returns deterministic scores by id."""
    def __init__(self, scores=None, default=9):
        self.captured = None
        self._scores = scores or {}
        self._default = default

    def score(self, draft, items):
        self.captured = list(items)
        return [Score(it.id, self._scores.get(it.id, self._default), "note") for it in items]


class PublicDefectGrounding(unittest.TestCase):
    def test_recycle_defect_carries_verified_card_and_line(self):
        pool = KBPool.single(lib.Config.load(FIX))
        r = services.execute(pool, "review", params={
            "draft": "d", "dimension": "pacing",
            "judge": FixtureJudge({}, default=(2, "weak"))})       # all below the bar -> Recycle
        rv = r.value
        self.assertEqual(rv.decision, "Recycle")
        statement, score, note, card, line = rv.defects[0]        # now 5-tuple: grounding carried through
        self.assertEqual(card, "brevity")                         # C1's anchor grounds in brevity
        src = (REPO / "tests" / "fixtures" / "kb-fixture" / "library" / "books" / "brevity.txt").read_text()
        self.assertIn("Trim every clause the reader can infer", src.splitlines()[line - 1])


class MultiAnchorGrounding(unittest.TestCase):
    def test_every_anchor_grounding_is_retained(self):
        cfg = lib.Config.load(FIX)
        crit, panel = load_criteria(cfg, "pacing")
        c2 = next(c for c in crit if len(c.markers) > 1)          # C2 anchors span brevity + structure
        g = ground(cfg, c2, panel)
        self.assertTrue(g.grounded)
        cards = {card for _, card, _ in g.anchors}
        self.assertEqual(cards, {"brevity", "structure"})        # both anchors kept, not just the first
        self.assertEqual(len(g.anchors), len(c2.markers))

    def test_a_criterion_with_no_anchors_is_not_grounded(self):
        from klode.gate import Criterion
        cfg = lib.Config.load(FIX)
        panel = load_criteria(cfg, "pacing")[1]
        self.assertFalse(ground(cfg, Criterion("X", "empty", ()), panel).grounded)


class JudgeSeam(unittest.TestCase):
    def test_unknown_criterion_id_fails_loud(self):
        cfg = lib.Config.load(FIX)

        class PhantomJudge:
            def score(self, draft, items):
                return [Score(it.id, 9, "ok") for it in items] + [Score("C99", 5, "phantom")]

        with self.assertRaises(ValueError):
            review_draft(cfg, "d", "pacing", PhantomJudge())

    def test_duplicate_score_id_fails_loud(self):
        cfg = lib.Config.load(FIX)

        class DupJudge:
            def score(self, draft, items):
                return [Score(it.id, 9, "n") for it in items] + [Score(items[0].id, 8, "dup")]

        with self.assertRaises(ValueError):
            review_draft(cfg, "d", "pacing", DupJudge())

    def test_missing_score_fails_loud(self):
        cfg = lib.Config.load(FIX)

        class ShortJudge:
            def score(self, draft, items):
                return [Score(items[0].id, 9, "n")]              # scores only the first — the rest are missing

        with self.assertRaises(ValueError):
            review_draft(cfg, "d", "pacing", ShortJudge())

    def test_ungrounded_compat_property(self):
        cfg = lib.Config.load(FIX2)
        v = review_draft(cfg, "d", "cadence", FixtureJudge({}, default=(9, "x")))
        self.assertEqual(v.ungrounded, tuple(cid for cid, _ in v.unavailable))

    def test_judge_receives_verified_evidence_bundle(self):
        cfg = lib.Config.load(FIX)
        spy = SpyJudge(default=9)
        v = review_draft(cfg, "d", "pacing", spy)
        self.assertEqual(v.decision, "Go")                        # deterministic verdict from the spy's scores
        self.assertTrue(spy.captured)
        for item in spy.captured:                                 # every item carries its verified evidence
            self.assertIsInstance(item.context, lib.ContextBundle)
            self.assertTrue(item.context.grounded)                # real spans from the real grounding path

    def test_judge_can_read_statement_and_guidance_off_the_grading_item(self):
        cfg = lib.Config.load(FIX)
        seen = []

        class ReadingJudge:
            def score(self, draft, items):
                seen.extend((it.statement, it.guidance) for it in items)
                return [Score(it.id, 9, "n") for it in items]

        review_draft(cfg, "d", "pacing", ReadingJudge())
        self.assertTrue(seen and all(stmt for stmt, _ in seen))    # statement delegates to the criterion

    def test_ungrounded_criterion_is_unavailable(self):
        cfg = lib.Config.load(FIX2)                               # unstamped cards + require_stamp default True
        v = review_draft(cfg, "d", "cadence", FixtureJudge({}, default=(9, "x")))
        self.assertEqual(v.decision, "Unavailable")
        self.assertIsNone(v.score)
        self.assertEqual(v.defects, ())
        # the same corpus grounds when the caller opts out of the stamp requirement
        self.assertIn(review_draft(cfg, "d", "cadence", FixtureJudge({}, default=(9, "x")),
                                   require_stamp=False).decision, ("Go", "Recycle"))

    def test_deterministic(self):
        cfg = lib.Config.load(FIX)
        a = review_draft(cfg, "d", "pacing", FixtureJudge({"C1": (2, "x")}, default=(9, "y")))
        b = review_draft(cfg, "d", "pacing", FixtureJudge({"C1": (2, "x")}, default=(9, "y")))
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
