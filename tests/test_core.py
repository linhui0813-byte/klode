"""WI-2 — core value types: provenance, scope, the six-value grounding resolution taxonomy, and the
discriminated lens results. A stdlib-only leaf that imports no adapter."""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from klode.lib import core                       # noqa: E402


class CoreTypes(unittest.TestCase):
    def test_provenance_round_trip(self):
        p = core.Provenance(op_id="verify", kb="storycraft", source_sha="abc", policy="p")
        self.assertEqual((p.op_id, p.kb, p.source_sha, p.policy), ("verify", "storycraft", "abc", "p"))
        self.assertEqual(p.op_version, "1")

    def test_resolution_has_exactly_the_six_taxonomy_values(self):
        self.assertEqual(
            {r.value for r in core.Resolution},
            {"found", "ambiguous", "folded-only", "source-stale", "source-not-installed", "not-found"})

    def test_capability_status_values(self):
        self.assertEqual({c.value for c in core.CapabilityStatus},
                         {"stable", "experimental", "unavailable"})

    def test_scope_constructors(self):
        self.assertEqual(core.Scope.registry().kind, "registry")
        self.assertEqual(core.Scope.kb("x").ids, ("x",))
        self.assertEqual(core.Scope.kbs(["a", "b"]).ids, ("a", "b"))

    def test_scope_rejects_malformed(self):
        for bad in (lambda: core.Scope.kb(""), lambda: core.Scope.kbs([]),
                    lambda: core.Scope("weird"), lambda: core.Scope("registry", ("x",))):
            with self.assertRaises(ValueError):
                bad()

    def test_lens_results_discriminate_by_type_not_string(self):
        d = core.DimensionResult("pacing", "canonical", "q", "craft", "[a]", "body")
        f = core.FrameworkResult("vega", "pacing", "T", "al", {})
        self.assertIsInstance(d, core.DimensionResult)
        self.assertNotIsInstance(f, core.DimensionResult)      # distinct types, not a magic outcome string

    def test_evidence_hit_is_occurrence_only(self):
        hit = core.EvidenceHit(core.Resolution.FOUND, "phrase")
        self.assertTrue(hit.found)
        self.assertTrue(hit.occurrence_only)                   # found != claim verified
        self.assertFalse(core.EvidenceHit(core.Resolution.NOT_FOUND, "p").found)

    def test_folded_only_counts_as_found(self):
        # a folded-only hit is a genuine occurrence (resolves across line/hyphenation folding)
        self.assertTrue(core.EvidenceHit(core.Resolution.FOLDED_ONLY, "p").found)


if __name__ == "__main__":
    unittest.main()
