"""The real judge: debiased, fail-loud, and unable to claim authority it has not earned.

Every test injects a fake transport — no network, no API key, fully deterministic. What is pinned
here is the behaviour that makes an LLM judge trustworthy rather than merely present:

  * two-step form-filling, with the steps derived BEFORE the draft is in view;
  * balanced permutation over reversed level orders, and honest reporting when the two disagree;
  * a malformed model reply fails loud instead of collapsing to a default score;
  * an uncalibrated judge cannot mark a verdict production-grade, and a calibration measured on a
    DIFFERENT rubric does not transfer.
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _rubric                                                             # noqa: E402
from klode import lib                                                      # noqa: E402
from klode.gate import review_draft, rubric_identity, load_spec            # noqa: E402
from klode.gate.llm_judge import (Calibration, JudgeError, LLMJudge,       # noqa: E402
                                  anthropic_transport)

FIX = REPO / "tests" / "fixtures" / "kb-fixture" / "library.toml"


class Item:
    """A stand-in GradingItem: the judge only reads id/statement/guidance/levels/context."""
    class _Lv:
        def __init__(self, score, text):
            self.score = score
            self.descriptor = type("F", (), {"value": text})()

    def __init__(self, cid="c.one", n=6):
        self.id = cid
        self.statement = "Cut what the reader can infer."
        self.guidance = "Judge against the surrounding text."
        self.levels = [self._Lv(i, f"band {i}") for i in range(n)]
        self.context = type("B", (), {"grounded": ()})()

    @property
    def max_score(self):
        return self.levels[-1].score


class Recorder:
    """A transport that records prompts and replays canned replies."""
    def __init__(self, replies):
        self.prompts = []
        self._replies = list(replies)

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self._replies.pop(0) if self._replies else '{"score": 3, "note": "ok"}'


class TwoStep(unittest.TestCase):
    def test_steps_are_derived_before_the_draft_and_reused(self):
        t = Recorder(["1. look\n2. count", '{"score":4,"note":"a"}', '{"score":4,"note":"a"}',
                      '{"score":2,"note":"b"}', '{"score":2,"note":"b"}'])
        j = LLMJudge(t, model="m")
        items = [Item("c.one")]
        j.score("DRAFT ONE", items)
        j.score("DRAFT TWO", items)                       # same criterion, second draft
        steps_prompts = [p for p in t.prompts if "evaluation steps" in p]
        self.assertEqual(len(steps_prompts), 1)          # derived once, memoized
        self.assertNotIn("DRAFT ONE", steps_prompts[0])  # and the draft was NOT in view
        self.assertNotIn("DRAFT TWO", steps_prompts[0])

    def test_the_form_prompt_carries_draft_steps_and_levels(self):
        t = Recorder(["1. look", '{"score":3,"note":"n"}', '{"score":3,"note":"n"}'])
        LLMJudge(t, model="m").score("THE DRAFT", [Item()])
        form = t.prompts[-1]
        for expected in ("THE DRAFT", "1. look", "band 0", "band 5", "0..5"):
            self.assertIn(expected, form)

    def test_no_evaluation_steps_fails_loud(self):
        j = LLMJudge(Recorder(["   "]), model="m")
        with self.assertRaises(JudgeError) as e:
            j.score("d", [Item()])
        self.assertIn("no evaluation steps", str(e.exception))


class BalancedPermutation(unittest.TestCase):
    def test_levels_are_presented_in_opposed_orders(self):
        t = Recorder(["steps", '{"score":4,"note":"x"}', '{"score":4,"note":"x"}'])
        LLMJudge(t, model="m", permutations=2).score("d", [Item()])
        forms = [p for p in t.prompts if "THE DRAFT" in p or "Score one draft" in p]
        self.assertEqual(len(forms), 2)
        first, second = (f[f.index("BEHAVIORAL LEVELS"):] for f in forms)
        self.assertLess(first.index("band 0"), first.index("band 5"))     # ascending
        self.assertGreater(second.index("band 0"), second.index("band 5"))  # reversed

    def test_opposed_runs_are_averaged_half_up(self):
        # 3 and 4 -> 3.5 -> 4 (half-up); banker's rounding would give 4 here too, so use 2 and 3
        t = Recorder(["steps", '{"score":2,"note":"x"}', '{"score":3,"note":"y"}'])
        s = LLMJudge(t, model="m").score("d", [Item()])[0]
        self.assertEqual(s.score, 3)                      # 2.5 half-up

    def test_order_sensitivity_is_surfaced_not_hidden(self):
        # a judge that answers differently by band order has not really decided; the averaged number
        # must not read as more settled than it is
        t = Recorder(["steps", '{"score":0,"note":"low"}', '{"score":5,"note":"high"}'])
        s = LLMJudge(t, model="m").score("d", [Item()])[0]
        self.assertEqual(s.score, 3)                      # 2.5 -> 3
        self.assertIn("order-sensitive", s.note)
        self.assertIn("[0, 5]", s.note)

    def test_agreement_between_runs_leaves_the_note_clean(self):
        t = Recorder(["steps", '{"score":4,"note":"settled"}', '{"score":4,"note":"settled"}'])
        s = LLMJudge(t, model="m").score("d", [Item()])[0]
        self.assertEqual(s.note, "settled")
        self.assertNotIn("order-sensitive", s.note)


class MalformedRepliesFailLoud(unittest.TestCase):
    """A gate that invents a score when the model failed is worse than one that abstains."""

    def _judge(self, reply):
        return LLMJudge(Recorder(["steps", reply]), model="m", permutations=1)

    def test_bad_replies_are_rejected(self):
        cases = [
            ("no json here at all", "no JSON object"),
            ('{"score": ', "no JSON object"),                 # truncated -> regex finds nothing
            ('{"note": "forgot the score"}', "must be an integer"),
            ('{"score": "4", "note": "n"}', "must be an integer"),
            ('{"score": 4.5, "note": "n"}', "must be an integer"),
            ('{"score": true, "note": "n"}', "must be an integer"),
            ('{"score": 9, "note": "n"}', "outside this criterion"),
            ('{"score": -1, "note": "n"}', "outside this criterion"),
        ]
        for reply, word in cases:
            with self.subTest(reply=reply), self.assertRaises(JudgeError) as e:
                self._judge(reply).score("d", [Item()])
            self.assertIn(word, str(e.exception))

    def test_json_embedded_in_prose_is_still_read(self):
        j = self._judge('Here is my answer:\n{"score": 4, "note": "fine"}\nHope that helps.')
        self.assertEqual(j.score("d", [Item()])[0].score, 4)

    def test_a_missing_note_is_tolerated_but_a_missing_score_is_not(self):
        self.assertEqual(self._judge('{"score": 2}').score("d", [Item()])[0].note, "")


class CalibrationGate(unittest.TestCase):
    def setUp(self):
        self.cfg = lib.Config.load(FIX)
        self.digest = rubric_identity(load_spec(self.cfg, "pacing"))

    def _judge(self, calibration=None):
        return LLMJudge(Recorder(["steps"] + ['{"score":5,"note":"n"}'] * 40),
                        model="m", calibration=calibration)

    def test_an_uncalibrated_judge_cannot_mark_a_verdict_production(self):
        v = review_draft(self.cfg, "d", "pacing", self._judge())
        self.assertIn(v.decision, ("Go", "Recycle"))
        self.assertFalse(v.calibrated)

    def test_a_calibration_on_this_rubric_that_clears_the_bar_sets_it(self):
        cal = Calibration(rubric_digest=self.digest, n=24, agreement=0.71)
        self.assertTrue(cal.clears())
        v = review_draft(self.cfg, "d", "pacing", self._judge(cal))
        self.assertTrue(v.calibrated)

    def test_a_calibration_measured_on_a_DIFFERENT_rubric_does_not_transfer(self):
        # rewording a level descriptor makes a different instrument; an agreement number from the
        # old one says nothing about the new one
        cal = Calibration(rubric_digest="0" * 64, n=50, agreement=0.95)
        self.assertTrue(cal.clears())
        self.assertFalse(cal.covers(self.digest))
        v = review_draft(self.cfg, "d", "pacing", self._judge(cal))
        self.assertFalse(v.calibrated)

    def test_too_few_drafts_or_too_little_agreement_do_not_clear(self):
        self.assertFalse(Calibration(self.digest, n=5, agreement=0.99).clears())    # tiny n
        self.assertFalse(Calibration(self.digest, n=100, agreement=0.4).clears())   # weak agreement
        self.assertTrue(Calibration(self.digest, n=100, agreement=0.4, bar=0.3).clears())

    def test_there_is_no_way_to_assert_calibration_without_a_record(self):
        j = self._judge()
        self.assertFalse(j.calibrated)
        self.assertFalse(j.calibrated_for(self.digest))
        # the only lever is supplying a measurement
        j.calibration = Calibration(self.digest, n=20, agreement=0.6)
        self.assertTrue(j.calibrated_for(self.digest))

    def test_the_stub_judge_is_never_calibrated(self):
        from klode.gate import FixtureJudge
        v = review_draft(self.cfg, "d", "pacing", FixtureJudge({}, default_fraction=1.0))
        self.assertFalse(v.calibrated)


class ReviewServiceHonesty(unittest.TestCase):
    def test_non_production_tracks_calibration_rather_than_being_hardcoded(self):
        from klode.lib import services
        from klode.lib.pool import KBPool
        cfg = lib.Config.load(FIX)
        pool = KBPool.single(cfg)
        digest = rubric_identity(load_spec(cfg, "pacing"))

        stub = services.execute(pool, "review", params={"draft": "d", "dimension": "pacing"})
        self.assertTrue(stub.value.non_production)          # no calibration -> not authoritative

        judge = LLMJudge(Recorder(["steps"] + ['{"score":5,"note":"n"}'] * 40), model="m",
                         calibration=Calibration(digest, n=30, agreement=0.8))
        real = services.execute(pool, "review",
                                params={"draft": "d", "dimension": "pacing", "judge": judge})
        self.assertFalse(real.value.non_production)         # measured -> may be presented as such


class Transport(unittest.TestCase):
    def test_a_missing_api_key_fails_loud_without_a_request(self):
        import os
        saved = os.environ.pop("KLODE_TEST_KEY", None)
        try:
            with self.assertRaises(JudgeError) as e:
                anthropic_transport("m", api_key_env="KLODE_TEST_KEY")("prompt")
            self.assertIn("KLODE_TEST_KEY", str(e.exception))
        finally:
            if saved is not None:
                os.environ["KLODE_TEST_KEY"] = saved

    def test_the_judge_requires_an_explicit_model_choice(self):
        # self-enhancement bias: the judge must be a different model than the author, which is a
        # decision the operator makes rather than inherits from a default
        import inspect
        sig = inspect.signature(LLMJudge.__init__)
        self.assertEqual(sig.parameters["model"].default, "")

    def test_permutations_must_be_at_least_one(self):
        with self.assertRaises(ValueError):
            LLMJudge(Recorder([]), model="m", permutations=0)


if __name__ == "__main__":
    unittest.main()
