"""Tests for the loopb walking skeleton — the chain (load criteria -> ground -> verdict) proven
deterministically with a FixtureJudge, against the real doxai worldbuilding dimension."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from lode import lib                                                    # noqa: E402
from lode.gate import Criterion, FixtureJudge, ground, load_criteria, review_draft   # noqa: E402

DOXAI = Path("/Users/joker/github/xiaolai/myprojects/lode/corpus/kb-01-storycraft/library.toml")


@unittest.skipUnless(DOXAI.exists(), "doxai library not present")
class WalkingSkeleton(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = lib.Config.load(DOXAI)

    def test_criteria_load_from_the_craft_layer(self):
        crit, panel = load_criteria(self.cfg, "worldbuilding")
        self.assertGreaterEqual(len(crit), 5)
        self.assertGreater(len(panel), 10)
        self.assertTrue(all(c.phrases for c in crit))            # every criterion cites a source

    def test_every_loaded_criterion_grounds_in_a_real_source(self):
        crit, panel = load_criteria(self.cfg, "worldbuilding")
        for c in crit:
            g = ground(self.cfg, c, panel)
            self.assertTrue(g.grounded, f"{c.id} {c.statement!r} failed to ground")
            self.assertIn(g.card, panel)

    def test_a_fabricated_citation_does_not_ground(self):
        _, panel = load_criteria(self.cfg, "worldbuilding")
        fake = Criterion("X", "made up", ("no source contains this exact zzqx sentence",))
        self.assertFalse(ground(self.cfg, fake, panel).grounded)   # the judge cannot fake a citation

    def test_recycle_with_grounded_defects(self):
        judge = FixtureJudge({"C1": (2, "info-dump"), "C2": (3, "no blanks")}, default=(5, "borderline"))
        v = review_draft(self.cfg, "some draft", "worldbuilding", judge)
        self.assertEqual(v.decision, "Recycle")
        self.assertTrue(v.defects)
        for l in v.defects:                                       # every defect is verifiable
            self.assertTrue(l.grounding.grounded)
            self.assertIsNotNone(l.grounding.card)

    def test_go_when_all_criteria_clear_the_hurdle(self):
        v = review_draft(self.cfg, "a strong draft", "worldbuilding",
                         FixtureJudge({}, default=(9, "strong")))
        self.assertEqual(v.decision, "Go")
        self.assertGreaterEqual(v.score, 60)

    def test_partial_fabrication_does_not_ground(self):
        # a criterion mixing one REAL citation with a fabricated one must NOT ground (every phrase counts)
        _, panel = load_criteria(self.cfg, "worldbuilding")
        real = "The status quo does not need world building"      # resolves in elliott-2013
        mixed = Criterion("Y", "mixed", (real, "no source contains this exact zzqx phrase"))
        self.assertFalse(ground(self.cfg, mixed, panel).grounded)

    def test_hurdle_out_of_range_is_rejected(self):
        with self.assertRaises(ValueError):
            review_draft(self.cfg, "d", "worldbuilding", FixtureJudge({}, default=(7, "ok")), hurdle=150)

    def test_out_of_range_judge_score_is_rejected(self):
        with self.assertRaises(ValueError):
            review_draft(self.cfg, "d", "worldbuilding", FixtureJudge({}, default=(50, "bad")))


if __name__ == "__main__":
    unittest.main()
