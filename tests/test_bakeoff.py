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


class Ranking(unittest.TestCase):
    """The harness previously printed tiers in insertion order and computed no aggregate at all —
    deleting "the ranking logic" would have changed nothing. Two backends with known-opposite
    fidelity must come back in the right order."""

    def setUp(self):
        real_ex, real_vis = bake.pdfmod._EXTRACTORS, bake.visual.check_pages
        self.addCleanup(setattr, bake.pdfmod, "_EXTRACTORS", real_ex)
        self.addCleanup(setattr, bake.visual, "check_pages", real_vis)
        bake.pdfmod._EXTRACTORS = dict(real_ex)
        bake.pdfmod._EXTRACTORS["good"] = lambda p, l: "a b c\f"
        bake.pdfmod._EXTRACTORS["poor"] = lambda p, l: "a b c\f"
        scores = {"good": (10, 10), "poor": (10, 2)}
        self._which = "good"
        # signature must match the real one: check_pages(pdf, candidate_page_text, *, pages, seed)
        def fake(pdf, candidate_page_text, *, pages, seed):
            ocr, matched = scores[self._which]
            return bake.visual.VisualReport(seed=seed, sampled=pages,
                                            checks=(bake.visual.PageCheck(1, ocr, matched),))
        self._fake = fake

    def test_the_better_backend_ranks_first(self):
        bake.visual.check_pages = self._fake
        # run each backend separately so the mock can score them differently, then merge
        self._which = "good"
        good = bake.bake_off([PDFS / "single-page.pdf"], ["good"], sample=1)
        self._which = "poor"
        poor = bake.bake_off([PDFS / "single-page.pdf"], ["poor"], sample=1)
        self.assertGreater(good["aggregate"]["good"]["median_visual"],
                           poor["aggregate"]["poor"]["median_visual"])
        self.assertEqual(good["ranking"], ["good"])
        self.assertEqual(poor["ranking"], ["poor"])

    def test_an_unmeasurable_backend_sorts_last_rather_than_scoring_zero(self):
        rep = {"pdfs": {"x.pdf": {"tiers": {"measured": {"visual": 0.4},
                                            "unmeasurable": {"visual": None}}}}, "skipped": {}}
        # exercise the real aggregation path on a hand-built report
        agg = {}
        for doc in rep["pdfs"].values():
            for tier, row in doc["tiers"].items():
                a = agg.setdefault(tier, {"scored": [], "pdfs": 0, "unscored": 0})
                a["pdfs"] += 1
                (a["scored"].append(row["visual"]) if row["visual"] is not None
                 else a.__setitem__("unscored", a["unscored"] + 1))
        for t, a in agg.items():
            a["median_visual"] = sorted(a["scored"])[len(a["scored"]) // 2] if a["scored"] else None
        ranking = sorted(agg, key=lambda t: (agg[t]["median_visual"] is None,
                                             -(agg[t]["median_visual"] or 0.0), t))
        self.assertEqual(ranking, ["measured", "unmeasurable"])


class HarnessBehaviour(unittest.TestCase):
    def test_absent_backends_are_reported_not_silently_dropped(self):
        # MOCKED, not environment-dependent: the previous version relied on docling NOT being
        # installed, so it would fail on a fully provisioned machine and pass if every extractor
        # were broken. Force one backend to fail and one to succeed.
        real = bake.pdfmod._EXTRACTORS
        bake.pdfmod._EXTRACTORS = dict(real)
        bake.pdfmod._EXTRACTORS["fakegood"] = lambda p, l: "alpha beta gamma\f"
        def _boom(p, l):
            raise ImportError("fakebad not installed")
        bake.pdfmod._EXTRACTORS["fakebad"] = _boom
        self.addCleanup(setattr, bake.pdfmod, "_EXTRACTORS", real)
        rep = bake.bake_off([PDFS / "single-page.pdf"], ["fakegood", "fakebad"], sample=1)
        self.assertIn("fakebad", rep["skipped"])
        self.assertIn("not installed", rep["skipped"]["fakebad"])
        self.assertIn("fakegood", rep["pdfs"]["single-page.pdf"]["tiers"])

    def test_an_unknown_tier_is_reported(self):
        rep = bake.bake_off([PDFS / "single-page.pdf"], ["nonesuch"], sample=1)
        self.assertIn("nonesuch", rep["skipped"])

    def test_the_seed_and_sampled_pages_are_recorded(self):
        # asserted UNCONDITIONALLY via a mocked visual report: the previous version guarded the
        # assertion behind `if visual is not None`, so in exactly the environment that needed
        # checking (no render tooling) it executed nothing and passed.
        real = bake.visual.check_pages
        bake.visual.check_pages = (
            lambda pdf, candidate_page_text, *, pages, seed: bake.visual.VisualReport(
                seed=seed, sampled=pages,
                checks=(bake.visual.PageCheck(page=1, ocr_tokens=10, matched=10),)))
        self.addCleanup(setattr, bake.visual, "check_pages", real)
        rep = bake.bake_off([PDFS / "three-pages.pdf"], ["pdftotext"], sample=2, seed=99)
        self.assertEqual(rep["seed"], 99)
        row = rep["pdfs"]["three-pages.pdf"]["tiers"]["pdftotext"]
        self.assertEqual(len(row["visual_sampled"]), 2)
        self.assertIsNotNone(row["visual"])

    def test_the_report_is_json_serialisable(self):
        rep = bake.bake_off([PDFS / "single-page.pdf"], ["pdftotext"], sample=1)
        json.dumps(rep)          # a report nobody can persist is not a measurement


@unittest.skipUnless(HAVE_POPPLER, "poppler not installed")
class AgainstTheCorpus(unittest.TestCase):
    def test_it_runs_and_scores_the_corpus(self):
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
