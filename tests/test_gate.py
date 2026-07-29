"""Tests for the loopb walking skeleton — the chain (load criteria -> ground -> verdict) proven
deterministically with a FixtureJudge, against the committed synthetic fixture KB under
tests/fixtures/. The KB path is resolved relative to this file, so the suite runs on ANY checkout
and actually exercises the chain (no absolute path, no dependency on the copyrighted corpus/ shelf,
no silent skip)."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from lode import lib                                                    # noqa: E402
from lode.gate import Criterion, FixtureJudge, ground, load_criteria, review_draft   # noqa: E402

FIXTURE = HERE / "fixtures" / "kb-fixture" / "library.toml"
DIM = "pacing"
REAL_PHRASE = "Trim every clause the reader can infer"                  # resolves in brevity.txt


class WalkingSkeleton(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = lib.Config.load(FIXTURE)

    def test_fixture_kb_is_present_and_portable(self):
        # the whole point of WI-2: the gate KB travels with the repo, resolved relative to __file__
        self.assertTrue(FIXTURE.is_file())
        self.assertTrue(str(FIXTURE.resolve()).startswith(str(HERE.parent)))   # inside the repo

    def test_criteria_load_from_the_craft_layer(self):
        crit, panel = load_criteria(self.cfg, DIM)
        self.assertEqual(len(crit), 3)                           # the three anchored Craft moves
        self.assertEqual(panel, ["brevity", "structure"])
        self.assertTrue(all(c.phrases for c in crit))            # every criterion cites a source

    def test_every_loaded_criterion_grounds_in_a_real_source(self):
        crit, panel = load_criteria(self.cfg, DIM)
        for c in crit:
            g = ground(self.cfg, c, panel)
            self.assertTrue(g.grounded, f"{c.id} {c.statement!r} failed to ground")
            self.assertIn(g.card, panel)

    def test_a_fabricated_citation_does_not_ground(self):
        _, panel = load_criteria(self.cfg, DIM)
        fake = Criterion("X", "made up", ("no source contains this exact zzqx sentence",))
        self.assertFalse(ground(self.cfg, fake, panel).grounded)   # the judge cannot fake a citation

    def test_recycle_with_grounded_defects(self):
        judge = FixtureJudge({"C1": (2, "info-dump"), "C2": (3, "no blanks")}, default=(5, "borderline"))
        v = review_draft(self.cfg, "some draft", DIM, judge)
        self.assertEqual(v.decision, "Recycle")
        self.assertTrue(v.defects)
        for l in v.defects:                                       # every defect is verifiable
            self.assertTrue(l.grounding.grounded)
            self.assertIsNotNone(l.grounding.card)

    def test_go_when_all_criteria_clear_the_hurdle(self):
        v = review_draft(self.cfg, "a strong draft", DIM,
                         FixtureJudge({}, default=(9, "strong")))
        self.assertEqual(v.decision, "Go")
        self.assertGreaterEqual(v.score, 60)

    def test_partial_fabrication_does_not_ground(self):
        # a criterion mixing one REAL citation with a fabricated one must NOT ground (every phrase counts)
        _, panel = load_criteria(self.cfg, DIM)
        mixed = Criterion("Y", "mixed", (REAL_PHRASE, "no source contains this exact zzqx phrase"))
        self.assertFalse(ground(self.cfg, mixed, panel).grounded)

    def test_hurdle_out_of_range_is_rejected(self):
        with self.assertRaises(ValueError):
            review_draft(self.cfg, "d", DIM, FixtureJudge({}, default=(7, "ok")), hurdle=150)

    def test_out_of_range_judge_score_is_rejected(self):
        with self.assertRaises(ValueError):
            review_draft(self.cfg, "d", DIM, FixtureJudge({}, default=(50, "bad")))


if __name__ == "__main__":
    unittest.main()
