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
from klode.gate.llm_judge import (FORM_PROMPT, PROMPT_VERSION, STEPS_PROMPT,   # noqa: E402
                                  Calibration, JudgeError, LLMJudge,
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
        cal = Calibration(rubric_digest=self.digest, n=24, agreement=0.71,
                          model="m", permutations=2, prompt_version=PROMPT_VERSION)
        self.assertTrue(cal.clears())
        v = review_draft(self.cfg, "d", "pacing", self._judge(cal))
        self.assertTrue(v.calibrated)

    def test_a_calibration_measured_on_a_DIFFERENT_rubric_does_not_transfer(self):
        # rewording a level descriptor makes a different instrument; an agreement number from the
        # old one says nothing about the new one
        cal = Calibration(rubric_digest="0" * 64, n=50, agreement=0.95,
                          model="m", permutations=2, prompt_version=PROMPT_VERSION)
        self.assertTrue(cal.clears())
        self.assertFalse(cal.covers(self.digest, model="m", permutations=2))
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
        j.calibration = Calibration(self.digest, n=20, agreement=0.6, model="m",
                                    permutations=2, prompt_version=PROMPT_VERSION)
        self.assertTrue(j.calibrated_for(self.digest))

    def test_the_stub_judge_is_never_calibrated(self):
        from klode.gate import FixtureJudge
        v = review_draft(self.cfg, "d", "pacing", FixtureJudge({}, default_fraction=1.0))
        self.assertFalse(v.calibrated)

    # --- the instrument is BOTH halves: the rubric AND the judge that reads it -------------
    def _cal(self, **kw):
        base = dict(rubric_digest=self.digest, n=30, agreement=0.8, model="m",
                    permutations=2, prompt_version=PROMPT_VERSION)
        base.update(kw)
        return Calibration(**base)

    def test_a_calibration_does_not_transfer_to_a_DIFFERENT_MODEL(self):
        """Self-enhancement bias is why `model` has no default. Measuring agreement through one
        model and inheriting it for another discards the reason that rule exists."""
        cal = self._cal(model="measured-model")
        j = LLMJudge(Recorder([]), model="a-different-model", permutations=2, calibration=cal)
        self.assertTrue(cal.clears())
        self.assertFalse(j.calibrated_for(self.digest))

    def test_a_calibration_does_not_transfer_ACROSS_PERMUTATION_POLICIES(self):
        """permutations=1 runs one forward order and cancels no position bias at all. An agreement
        number measured at 2 describes a different instrument."""
        cal = self._cal(permutations=2)
        for perms in (1, 4):
            with self.subTest(permutations=perms):
                j = LLMJudge(Recorder([]), model="m", permutations=perms, calibration=cal)
                self.assertFalse(j.calibrated_for(self.digest))

    def test_a_calibration_does_not_survive_a_PROMPT_CHANGE(self):
        cal = self._cal(prompt_version="0")
        j = LLMJudge(Recorder([]), model="m", permutations=2, calibration=cal)
        self.assertFalse(j.calibrated_for(self.digest))

    def test_a_record_from_before_the_instrument_was_pinned_fails_closed(self):
        """An existing record still constructs — and claims nothing, because None matches no live
        judge. Losing a claim is the correct migration; keeping an unmeasured one is the defect."""
        legacy = Calibration(self.digest, n=30, agreement=0.8)
        self.assertTrue(legacy.clears())
        j = LLMJudge(Recorder([]), model="m", permutations=2, calibration=legacy)
        self.assertFalse(j.calibrated_for(self.digest))

    def test_the_matching_instrument_still_clears(self):
        """The whole point is that a real measurement still counts."""
        j = LLMJudge(Recorder([]), model="m", permutations=2, calibration=self._cal())
        self.assertTrue(j.calibrated_for(self.digest))

    def test_a_serialized_record_round_trips_and_keeps_its_verdict(self):
        """`Calibration` has no persistence path today — it is built in code and never written —
        so "an old stored record fails closed" was an untested claim about a file that cannot
        exist. This pins the shape any future persistence would take: a record serialized WITHOUT
        the instrument fields reconstructs and claims nothing, and one serialized WITH them
        reconstructs and still clears."""
        import dataclasses
        legacy = {"rubric_digest": self.digest, "n": 30, "agreement": 0.8,
                  "bar": 0.6, "min_n": 20, "measured_on": "2026-01-01"}
        j = LLMJudge(Recorder([]), model="m", permutations=2,
                     calibration=Calibration(**legacy))
        self.assertTrue(j.calibration.clears())            # the measurement itself is intact
        self.assertFalse(j.calibrated_for(self.digest))    # but it describes no known instrument

        full = dataclasses.asdict(self._cal())
        self.assertEqual(set(full) - set(legacy), {"model", "permutations", "prompt_version"})
        j2 = LLMJudge(Recorder([]), model="m", permutations=2, calibration=Calibration(**full))
        self.assertTrue(j2.calibrated_for(self.digest))


class PermutationsMustActuallyBalance(unittest.TestCase):
    """`reverse=bool(i % 2)` splits an odd count unevenly. At 3 that is two forward runs and one
    reversed, so the mean keeps a share of the position bias the averaging exists to cancel — at
    1.5x the API cost of the even count below it. The trap is that 3 reads as more thorough than 2
    and is strictly less debiased."""

    def _orders(self, permutations):
        t = Recorder(["steps"] + ['{"score":3,"note":"n"}'] * 20)
        LLMJudge(t, model="m", permutations=permutations).score("d", [Item()])
        forms = [p for p in t.prompts if "Score one draft" in p]
        out = []
        for f in forms:
            block = f[f.index("BEHAVIORAL LEVELS"):]
            out.append("forward" if block.index("band 0") < block.index("band 5") else "reversed")
        return out

    def test_every_permitted_count_above_one_is_balanced(self):
        for p in LLMJudge.VALID_PERMUTATIONS:
            if p == 1:
                continue
            with self.subTest(permutations=p):
                orders = self._orders(p)
                self.assertEqual(orders.count("forward"), orders.count("reversed"),
                                 f"permutations={p} presents {orders.count('forward')} forward "
                                 f"and {orders.count('reversed')} reversed")

    def test_one_is_permitted_and_is_a_single_forward_pass(self):
        """Kept legal on purpose — it is the cheap, explicitly undebiased mode — but it must be
        honest about being one pass rather than pretending to cancel anything."""
        self.assertEqual(self._orders(1), ["forward"])

    def test_an_odd_count_above_one_is_refused(self):
        for bad in (3, 5, 15):
            with self.subTest(permutations=bad):
                with self.assertRaises(ValueError) as cm:
                    LLMJudge(Recorder([]), model="m", permutations=bad)
                self.assertIn("even", str(cm.exception))

    def test_a_bool_does_not_slip_through_as_one(self):
        """`permutations < 1` was the entire guard, and `True < 1` is False — so `True` constructed
        and silently ran one forward pass while looking like a configuration mistake nobody saw."""
        with self.assertRaises(ValueError) as cm:
            LLMJudge(Recorder([]), model="m", permutations=True)
        self.assertIn("integer", str(cm.exception))

    def test_a_float_fails_at_the_constructor_not_inside_range(self):
        with self.assertRaises(ValueError):
            LLMJudge(Recorder([]), model="m", permutations=2.0)

    def test_a_value_above_the_settings_cap_is_refused_here_too(self):
        """The constructor and the settings domain have to agree, or whichever is looser is the
        real policy and the other one is decoration."""
        from klode.lib import settings
        spec = next(s for s in settings.SPEC if s.key == "permutations")
        self.assertEqual(tuple(spec.choices), LLMJudge.VALID_PERMUTATIONS)
        with self.assertRaises(ValueError):
            LLMJudge(Recorder([]), model="m", permutations=17)


class TheInstrumentIsBoundToWhatItActuallyDoes(unittest.TestCase):
    def test_cached_steps_do_not_cross_a_rubric_revision(self):
        """A criterion id is deliberately STABLE across revisions — that is what makes human
        labels survive an edit. Caching steps by id alone therefore reused the standard derived
        for the old wording, so a revised criterion was scored against steps nobody wrote for it."""
        t = Recorder([])
        j = LLMJudge(t, model="m", permutations=2)
        a, b = Item("c.one"), Item("c.one")
        b.statement = "An entirely different criterion under the same stable id."
        j.score("d", [a])
        j.score("d", [b])
        steps = [p for p in t.prompts if "evaluation steps" in p]
        self.assertEqual(len(steps), 2, "the revised criterion reused the old steps")

    def test_the_same_criterion_is_still_derived_only_once(self):
        """The memoization must survive the fix, or every draft pays for fresh steps."""
        t = Recorder([])
        j = LLMJudge(t, model="m", permutations=2)
        j.score("draft one", [Item("c.one")])
        j.score("draft two", [Item("c.one")])
        self.assertEqual(len([p for p in t.prompts if "evaluation steps" in p]), 1)

    def test_a_transport_that_declares_a_different_model_is_refused(self):
        """`self.model` is the label a Calibration matches against, and it was only ever a CLAIM —
        the transport is injectable, so it could send something else entirely and the record would
        still certify."""
        t = anthropic_transport("claude-opus-5")
        with self.assertRaises(ValueError) as cm:
            LLMJudge(t, model="a-different-label", permutations=2)
        self.assertIn("transport sends model", str(cm.exception))
        LLMJudge(t, model="claude-opus-5", permutations=2)          # matching label is fine
        LLMJudge(lambda p: "", model="anything", permutations=2)    # undeclaring stays allowed

    def test_calibrated_and_calibrated_for_never_disagree(self):
        """`calibrated` checked only that the RECORD cleared its bar, so a direct consumer got
        True for exactly the cases `calibrated_for` refuses."""
        cfg = lib.Config.load(FIX)
        digest = rubric_identity(load_spec(cfg, "pacing"))
        base = dict(rubric_digest=digest, n=30, agreement=0.8)
        for label, cal in (
                ("legacy record", Calibration(**base)),
                ("wrong model", Calibration(**base, model="other", permutations=2,
                                            prompt_version=PROMPT_VERSION)),
                ("wrong permutations", Calibration(**base, model="m", permutations=4,
                                                   prompt_version=PROMPT_VERSION)),
                ("stale prompt", Calibration(**base, model="m", permutations=2,
                                             prompt_version="0"))):
            with self.subTest(label=label):
                j = LLMJudge(Recorder([]), model="m", permutations=2, calibration=cal)
                self.assertEqual(j.calibrated, j.calibrated_for(digest))
                self.assertFalse(j.calibrated)

    def test_a_bool_or_float_in_a_record_does_not_match_a_real_policy(self):
        """`True == 1` and `2.0 == 2` in Python, so a record carrying either covered a live policy
        the judge itself refuses to be constructed with."""
        cfg = lib.Config.load(FIX)
        digest = rubric_identity(load_spec(cfg, "pacing"))
        for bad in (True, 2.0):
            with self.subTest(permutations=bad):
                cal = Calibration(digest, n=30, agreement=0.8, model="m",
                                  permutations=bad, prompt_version=PROMPT_VERSION)
                j = LLMJudge(Recorder([]), model="m", permutations=2, calibration=cal)
                self.assertFalse(j.calibrated_for(digest))


class ThePromptsAreTheInstrument(unittest.TestCase):
    """The tripwire behind PROMPT_VERSION. The prompts ARE the judge: reword one and it answers
    differently, so a stored calibration stops describing it. Nothing in a dataclass can notice
    that, so the check has to live here — edit a prompt without bumping the constant and this
    fails, rather than the calibration silently outliving the instrument it measured."""

    DIGESTS = {
        "1": ("40944a2d7d5b9edb", "templates + render/aggregate code as of PROMPT_VERSION 1"),
    }

    @staticmethod
    def _instrument_digest() -> str:
        """The templates AND the code that renders and aggregates them.

        Hashing the two literals alone left the instrument half-covered: `_levels_block`,
        `_evidence_block` and the averaging in `score` can each change the prompt a model sees or
        the number it produces, without either template moving. Verified — editing `_levels_block`
        and the rounding changed a rendered prompt and turned a 2.5 into 2, while the template
        hash, the version and `calibrated_for()` all stayed put."""
        import hashlib
        import inspect
        from klode.gate import llm_judge as lj
        parts = [STEPS_PROMPT, FORM_PROMPT,
                 inspect.getsource(lj._levels_block),
                 inspect.getsource(lj._evidence_block),
                 inspect.getsource(lj.LLMJudge.score),
                 inspect.getsource(lj.LLMJudge._one)]
        return hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:16]

    def test_a_prompt_edit_requires_a_version_bump(self):
        actual = self._instrument_digest()
        known = self.DIGESTS.get(PROMPT_VERSION)
        self.assertIsNotNone(
            known, f"PROMPT_VERSION is {PROMPT_VERSION!r} but no digest is recorded for it — add "
                   f"one: {actual!r}")
        self.assertEqual(
            actual, known[0],
            "STEPS_PROMPT/FORM_PROMPT changed without bumping PROMPT_VERSION. Every stored "
            "Calibration was measured through the OLD wording and no longer describes this judge. "
            f"Bump PROMPT_VERSION and record the new digest {actual!r}.")


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
                         calibration=Calibration(digest, n=30, agreement=0.8, model="m",
                                                 permutations=2,
                                                 prompt_version=PROMPT_VERSION))
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
        # Self-enhancement bias: the judge must be a different model than the author, so the
        # operator must choose. The previous version of this test asserted only that the DEFAULT
        # was "" — which is not the same claim, and let `LLMJudge(t)` construct with no model at
        # all while the docs said `model` was required. Assert the refusal, not the default.
        for bad in ("", "   "):
            with self.subTest(model=bad), self.assertRaises(ValueError) as e:
                LLMJudge(Recorder([]), model=bad)
            self.assertIn("requires an explicit `model`", str(e.exception))
        LLMJudge(Recorder([]), model="some-model")          # an explicit choice is accepted

    def test_permutations_must_be_at_least_one(self):
        with self.assertRaises(ValueError):
            LLMJudge(Recorder([]), model="m", permutations=0)


if __name__ == "__main__":
    unittest.main()
