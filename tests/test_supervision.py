"""WI-6 — supervision (`review`) is capability-gated: a stub-judge verdict can never be presented as
authoritative. Authoring ops are registered and delegate to the existing engine modules."""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from klode import gate                                  # noqa: E402
from klode.lib import check as check_mod, core, opspec, services   # noqa: E402
from klode.lib.config import Config                     # noqa: E402
from klode.lib.pool import KBPool                        # noqa: E402

FIX = REPO / "tests" / "fixtures" / "kb-fixture" / "library.toml"    # dim "pacing"


class Supervision(unittest.TestCase):
    def setUp(self):
        self.cfg = Config.load(FIX)
        self.pool = KBPool.single(self.cfg)

    def _review(self, **params):
        params.setdefault("dimension", "pacing")
        return services.execute(self.pool, "review", params=params)

    def test_review_self_labels_non_production(self):
        r = self._review(draft="a draft")
        self.assertIsInstance(r.value, core.ReviewResult)
        self.assertTrue(r.value.non_production)
        self.assertEqual(r.value.judge_identity, "FixtureJudge")     # the stub identifies itself

    def test_review_capability_is_experimental(self):
        r = self._review(draft="a draft")
        self.assertIs(r.capability, core.CapabilityStatus.EXPERIMENTAL)   # from the registry
        self.assertIs(r.value.capability, core.CapabilityStatus.EXPERIMENTAL)

    def test_refuse_non_production_yields_capability_unavailable(self):
        r = self._review(draft="a draft", refuse_non_production=True)
        self.assertIs(r.value.capability, core.CapabilityStatus.UNAVAILABLE)
        self.assertIsNone(r.value.decision)                          # no verdict body

    def test_no_authoritative_verdict_without_the_envelope(self):
        # a Recycle from the stub still carries non_production=True — it can never look authoritative
        judge = gate.FixtureJudge({"pacing.cut-inferable": (1, "info-dump")}, default_fraction=0.4)
        r = self._review(draft="a draft", judge=judge)
        self.assertEqual(r.value.decision, "Recycle")
        self.assertTrue(r.value.non_production)

    def test_authoring_check_delegates_unchanged(self):
        via_op = services.execute(self.pool, "check").value
        direct = check_mod.check(self.cfg)
        self.assertEqual(via_op.errors, direct.errors)               # same Report, same (zero) errors
        self.assertEqual(via_op.errors, [])

    def test_authoring_ops_registered(self):
        for auth in ("init", "build", "check", "normalize", "ingest"):
            self.assertIsNotNone(opspec.by_op_id(auth), auth)


if __name__ == "__main__":
    unittest.main()
