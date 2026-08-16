"""The hurdle is a threshold on the score, not on a rendering of it.

`review.py` rounded the exact mean and compared THAT to the hurdle, so every draft whose true
score fell in `[hurdle-0.5, hurdle)` was promoted. A 0..3 / 0..7 rubric scored 1 and 6 is exactly
59.5238% and returned Go at a hurdle of 60.

The error ran one way only. Across every two-criterion rubric with maxima 2..10 there are 12
false-Go configurations and no false-Recycle — a false Recycle cannot occur, because rounding can
only raise a value across an integer boundary from below. A gate documented as fail-CLOSED erred
open, and only open.

`Verdict.defects` had already been fixed for this exact reason, with a comment saying no criterion
should cross the bar purely by rounding. The verdict itself was still doing it.

Both other fixture rubrics use uniform scales — 3x0..5 and 2x0..10 — whose means always land on an
integer percentage, so no existing test could express the defect. `mixed-scale.json` exists to
close that gap and is checked by CI alongside the other two.
"""
from __future__ import annotations

import math
import shutil
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from klode import lib                                                     # noqa: E402
from klode.gate import review_draft                                       # noqa: E402
from klode.gate.judge import Score                                        # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "kb-fixture"


class _FixedJudge:
    """Returns a preset level for each criterion — the draft is irrelevant here; the arithmetic
    between the judge's levels and the verdict is what is under test."""

    def __init__(self, by_id):
        self.by_id = by_id

    def score(self, draft, items):
        return [Score(it.id, self.by_id[it.id], "fixed") for it in items]


class _MixedScaleKB(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        shutil.copytree(FIXTURE, self.tmp / "kb")
        self.cfg = lib.Config.load(self.tmp / "kb" / "library.toml")

    def _verdict(self, a, b, hurdle=60):
        judge = _FixedJudge({"mixed-scale.cut-inferable": a,
                             "mixed-scale.vary-sentence-length": b})
        v = review_draft(self.cfg, "a draft", "mixed-scale", judge,
                         hurdle=hurdle, require_stamp=False)
        self.assertNotEqual(v.decision, "Unavailable", f"grounding failed: {v.unavailable}")
        return v

    @staticmethod
    def _exact(v):
        return 100 * sum(Fraction(l.score, l.max_score) for l in v.lines) / len(v.lines)


class ASubHurdleDraftIsRecycled(_MixedScaleKB):
    def test_the_case_that_used_to_return_go(self):
        v = self._verdict(1, 6, hurdle=60)               # exactly 59.5238%
        self.assertLess(self._exact(v), 60)
        self.assertEqual(v.decision, "Recycle",
                         f"a draft at {float(self._exact(v)):.4f}% passed a hurdle of 60")

    def test_a_draft_exactly_at_the_hurdle_still_passes(self):
        """Correcting an off-by-a-rounding must not turn into an off-by-one the other way."""
        v = self._verdict(3, 0, hurdle=50)               # (100% + 0%) / 2 = exactly 50%
        self.assertEqual(self._exact(v), 50)
        self.assertEqual(v.decision, "Go")


class TheShownScoreNeverContradictsTheDecision(_MixedScaleKB):
    def test_the_displayed_score_is_the_floor(self):
        v = self._verdict(1, 6, hurdle=60)
        self.assertEqual(v.score, math.floor(self._exact(v)))
        self.assertLess(v.score, v.hurdle)               # 59, and Recycle — consistent

    def test_display_and_decision_agree_across_the_whole_grid(self):
        """The property the floor was chosen for: for an integer hurdle,
        `floor(x) >= h  <=>  x >= h`, so the number shown can never disagree with the verdict.
        Rounding breaks this in 1338 of these same cases."""
        for hurdle in (0, 1, 33, 50, 60, 61, 99, 100):
            for a in range(4):
                for b in range(8):
                    with self.subTest(hurdle=hurdle, a=a, b=b):
                        v = self._verdict(a, b, hurdle=hurdle)
                        self.assertEqual(v.decision == "Go", v.score >= v.hurdle,
                                         f"score {v.score} vs hurdle {v.hurdle} "
                                         f"contradicts {v.decision}")

    def test_the_decision_matches_the_exact_mean_across_the_whole_grid(self):
        for hurdle in (0, 33, 50, 60, 61, 100):
            for a in range(4):
                for b in range(8):
                    with self.subTest(hurdle=hurdle, a=a, b=b):
                        v = self._verdict(a, b, hurdle=hurdle)
                        expected = "Go" if self._exact(v) >= hurdle else "Recycle"
                        self.assertEqual(v.decision, expected)


class TheHurdleMustBeAnInteger(_MixedScaleKB):
    """The floor property holds only for an integer hurdle. Range was checked; type was not, so
    `hurdle=59.6` was accepted and would have reopened the same gap between shown and decided."""

    def test_a_float_hurdle_is_refused(self):
        for bad in (59.6, 60.0, True):
            with self.subTest(hurdle=bad):
                with self.assertRaises(ValueError):
                    review_draft(self.cfg, "d", "mixed-scale", _FixedJudge({}),
                                 hurdle=bad, require_stamp=False)

    def test_an_out_of_range_integer_is_still_refused(self):
        for bad in (-1, 101):
            with self.subTest(hurdle=bad):
                with self.assertRaises(ValueError):
                    review_draft(self.cfg, "d", "mixed-scale", _FixedJudge({}),
                                 hurdle=bad, require_stamp=False)


class TheServiceBoundaryDoesNotLaunderTheHurdle(_MixedScaleKB):
    """`review_draft` refuses a non-integer hurdle. The review SERVICE coerced with `int(...)`
    first, so the guard never saw one — and truncation always moves a fractional hurdle DOWN. Ask
    for 59.6 and the gate silently applied 59, passing a draft that scored 59.5238. `int(False)`
    is 0, which passes everything. MCP and CLI both go through this path, so the guard was
    decorative exactly where it mattered."""

    def _svc(self, hurdle):
        from klode.lib import services
        from klode.lib.pool import KBPool
        judge = _FixedJudge({"mixed-scale.cut-inferable": 1,
                             "mixed-scale.vary-sentence-length": 6})
        return services.execute(KBPool.single(self.cfg), "review",
                                params={"draft": "d", "dimension": "mixed-scale",
                                        "hurdle": hurdle, "judge": judge})

    def test_a_fractional_hurdle_is_refused_not_truncated(self):
        for bad in (59.6, 60.5, "60", True, False):
            with self.subTest(hurdle=bad), self.assertRaises(ValueError):
                self._svc(bad)

    def test_a_valid_hurdle_still_reaches_the_verdict(self):
        v = self._svc(60).value
        self.assertEqual((v.decision, v.score), ("Recycle", 59))


class PerCriterionDisplayAgreesWithClassification(_MixedScaleKB):
    """`Verdict.defects` classifies by exact ratio; `Line.pct` rounded. So a criterion could show
    AT the hurdle while being listed as a defect below it — the same contradiction I fixed in the
    verdict and left in the two other places the arithmetic appears."""

    def test_a_criterion_shown_at_or_above_the_hurdle_is_never_a_defect(self):
        for a in range(4):
            for b in range(8):
                for hurdle in (33, 50, 60, 67, 75):
                    with self.subTest(a=a, b=b, hurdle=hurdle):
                        v = self._verdict(a, b, hurdle=hurdle)
                        for line in v.defects:
                            self.assertLess(line.pct, v.hurdle,
                                            f"{line.score}/{line.max_score} displays as "
                                            f"{line.pct} at hurdle {v.hurdle} yet is a defect")


class TheFixtureItselfMixesScales(_MixedScaleKB):
    """If this fixture ever drifts back to uniform scales it stops being able to express the
    defect, and the tests above would pass for the wrong reason."""

    def test_the_two_criteria_have_different_maxima(self):
        v = self._verdict(1, 6)
        maxima = sorted(l.max_score for l in v.lines)
        self.assertEqual(maxima, [3, 7])
        self.assertNotEqual(maxima[0], maxima[1],
                            "the mixed-scale fixture no longer mixes scales")


if __name__ == "__main__":
    unittest.main()
