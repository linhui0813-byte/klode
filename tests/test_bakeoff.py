"""WI-7 — the bake-off, and proof that its rejected metric had to be rejected.

The plan's check: *"backend ranking must not reverse when anchors are chosen from a different
backend's output. If it does, the metric measured authoring compatibility, not fidelity."* These
tests run that experiment and show the reversal happening, which is why anchor-resolution is
reported as a migration statistic and never ranked on.
"""
import importlib.util
import io
import json
import shutil
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

_spec = importlib.util.spec_from_file_location("klode_bakeoff", REPO / "eval" / "extract_bakeoff.py")
bake = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bake)

PDFS = REPO / "tests" / "fixtures" / "pdfs"
HAVE_POPPLER = bool(shutil.which("pdftotext") and shutil.which("pdfinfo"))


class AnchorCompatibilityIsBiased(unittest.TestCase):
    """Why it cannot be the decider — demonstrated, not asserted."""

    A = "the module hides substantial complexity behind a small interface alpha beta gamma"
    B = "the module conceals considerable complexity behind a compact interface delta epsilon zeta"

    def test_the_ranking_reverses_with_the_anchors_origin(self):
        # anchors authored against A
        from_a = ["alpha beta gamma", "hides substantial complexity"]
        a_wins = (bake.anchor_compatibility(self.A, from_a),
                  bake.anchor_compatibility(self.B, from_a))
        # the same experiment, anchors authored against B
        from_b = ["delta epsilon zeta", "conceals considerable complexity"]
        b_wins = (bake.anchor_compatibility(self.A, from_b),
                  bake.anchor_compatibility(self.B, from_b))

        self.assertGreater(a_wins[0], a_wins[1])      # with A's anchors, A wins
        self.assertLess(b_wins[0], b_wins[1])         # with B's anchors, B wins
        # the winner flipped purely with the anchors' origin: the metric measures compatibility
        # with the authoring backend, not fidelity to the page
        self.assertNotEqual(a_wins[0] > a_wins[1], b_wins[0] > b_wins[1])

    def test_it_is_blind_to_reading_order(self):
        # a fully scrambled extraction resolves every anchor — the exact failure the effort exists
        # to catch, invisible to the metric v1 chose as decisive
        toks = [f"tok{i}" for i in range(200)]
        clean = " ".join(toks)
        scrambled = " ".join(reversed(toks))
        anchors = ["tok10", "tok50", "tok150"]
        self.assertEqual(bake.anchor_compatibility(clean, anchors), 1.0)
        self.assertEqual(bake.anchor_compatibility(scrambled, anchors), 1.0)

    def test_no_anchors_yields_None_not_a_perfect_score(self):
        self.assertIsNone(bake.anchor_compatibility("anything", []))


class HarnessBehaviour(unittest.TestCase):
    def test_absent_backends_are_reported_not_silently_dropped(self):
        rep = bake.bake_off([PDFS / "single-page.pdf"], ["pdftotext", "docling", "xberg"], sample=1)
        # docling/kreuzberg are not installed in this environment
        self.assertTrue(rep["skipped"], "an untested backend must be named")
        for tier, why in rep["skipped"].items():
            self.assertTrue(why, f"{tier} skipped with no reason given")

    def test_an_unknown_tier_is_reported(self):
        rep = bake.bake_off([PDFS / "single-page.pdf"], ["nonesuch"], sample=1)
        self.assertIn("nonesuch", rep["skipped"])

    def test_the_seed_and_sampled_pages_are_recorded(self):
        rep = bake.bake_off([PDFS / "three-pages.pdf"], ["pdftotext"], sample=2, seed=99)
        self.assertEqual(rep["seed"], 99)
        row = rep["pdfs"]["three-pages.pdf"]["tiers"].get("pdftotext")
        if row and row.get("visual") is not None:
            self.assertEqual(len(row["visual_sampled"]), 2)

    def test_the_report_is_json_serialisable(self):
        rep = bake.bake_off([PDFS / "single-page.pdf"], ["pdftotext"], sample=1)
        json.dumps(rep)          # a report nobody can persist is not a measurement


@unittest.skipUnless(HAVE_POPPLER, "poppler not installed")
class AgainstTheCorpus(unittest.TestCase):
    def test_it_runs_and_ranks_by_visual_fidelity(self):
        rep = bake.bake_off(sorted(PDFS.glob("*.pdf")), ["pdftotext"], sample=1)
        self.assertEqual(len(rep["pdfs"]), 5)
        scored = [r["tiers"]["pdftotext"]["visual"] for r in rep["pdfs"].values()
                  if "pdftotext" in r["tiers"] and r["tiers"]["pdftotext"].get("visual") is not None]
        self.assertTrue(scored, "no PDF produced a visual score")
        self.assertGreater(min(scored), 0.9)          # the control is faithful on a clean corpus

    def test_the_cli_names_its_ranking_column_and_its_caveat(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            bake.main(["--pdfs", str(PDFS / "single-page.pdf"), "--tiers", "pdftotext",
                       "--sample", "1"])
        out = buf.getvalue()
        self.assertIn("Ranking is by `visual`", out)
        self.assertIn("Never rank on it", out)        # the caveat travels with the numbers


if __name__ == "__main__":
    unittest.main()
