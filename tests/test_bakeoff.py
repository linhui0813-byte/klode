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
import tempfile
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

    # Each backend emits DISTINGUISHABLE text so one mocked `check_pages` can score them
    # differently within a SINGLE bake_off run. Running each backend in its own one-tier report and
    # asserting `ranking == ["good"]` proved only that a list of one sorts to itself — production
    # ranking between two backends was never exercised.
    GOOD, POOR, REVERSED = "g1 g2 g3\f", "p1 p2 p3\f", "r1 r2 r3\f"

    def setUp(self):
        real_ex, real_vis = bake.pdfmod._EXTRACTORS, bake.visual.check_pages
        self.addCleanup(setattr, bake.pdfmod, "_EXTRACTORS", real_ex)
        self.addCleanup(setattr, bake.visual, "check_pages", real_vis)
        bake.pdfmod._EXTRACTORS = dict(real_ex)
        bake.pdfmod._EXTRACTORS["good"] = lambda p, l: self.GOOD
        bake.pdfmod._EXTRACTORS["poor"] = lambda p, l: self.POOR
        bake.pdfmod._EXTRACTORS["reversed"] = lambda p, l: self.REVERSED
        bake.pdfmod._EXTRACTORS["unmeasurable"] = lambda p, l: "u1 u2 u3\f"

        # (ocr_tokens, matched, order) keyed by the candidate text, so the mock scores whichever
        # backend it is actually being called for
        table = {self.GOOD.strip(): (10, 10, 1.0),
                 self.POOR.strip(): (10, 2, 1.0),
                 self.REVERSED.strip(): (10, 10, -1.0)}

        def fake(pdf, candidate_page_text, *, pages, seed):
            key = "".join(candidate_page_text.values()).strip()
            if key not in table:            # the "unmeasurable" backend: ran, produced no score
                return bake.visual.VisualReport(seed=seed, sampled=pages,
                                                checks=(bake.visual.PageCheck(1, 0, 0, error="no ocr"),))
            ocr, matched, order = table[key]
            return bake.visual.VisualReport(
                seed=seed, sampled=pages,
                checks=(bake.visual.PageCheck(1, ocr, matched, order=order),))
        self._fake = fake

    def test_the_better_backend_ranks_first(self):
        bake.visual.check_pages = self._fake
        rep = bake.bake_off([PDFS / "single-page.pdf"], ["poor", "good"], sample=1)
        self.assertEqual(rep["ranking"], ["good", "poor"])       # NOT insertion order
        self.assertGreater(rep["aggregate"]["good"]["median_visual"],
                           rep["aggregate"]["poor"]["median_visual"])

    def test_a_backend_with_inverted_reading_order_is_demoted_below_a_worse_recall(self):
        # recall is order-blind: `reversed` matches every OCR token and outscores `poor` on recall
        # alone. Ranking must still put it last — the scrambling is the failure this harness exists
        # to catch, and it was measured and then discarded.
        bake.visual.check_pages = self._fake
        rep = bake.bake_off([PDFS / "single-page.pdf"], ["reversed", "poor"], sample=1)
        agg = rep["aggregate"]
        self.assertGreater(agg["reversed"]["median_visual"], agg["poor"]["median_visual"])
        self.assertTrue(agg["reversed"]["order_inverted"])
        self.assertEqual(rep["ranking"], ["poor", "reversed"])

    def test_an_unmeasurable_backend_sorts_last_rather_than_scoring_zero(self):
        # through the REAL bake_off. The previous version reimplemented the aggregation inline, so
        # deleting the production ranking left it green.
        bake.visual.check_pages = self._fake
        rep = bake.bake_off([PDFS / "single-page.pdf"], ["unmeasurable", "poor"], sample=1)
        self.assertIsNone(rep["aggregate"]["unmeasurable"]["median_visual"])
        self.assertEqual(rep["ranking"], ["poor", "unmeasurable"])

    def test_the_reported_median_is_a_median_not_the_upper_of_two(self):
        # `sorted(v)[len(v)//2]` reported 1.0 for [0.0, 1.0] — the better score presented as the
        # midpoint of the pair, which flatters a backend that failed half its documents.
        self.assertEqual(bake._median([0.0, 1.0]), 0.5)
        self.assertEqual(bake._median([0.2, 0.4, 0.9]), 0.4)
        self.assertIsNone(bake._median([None, None]))


class MarkdownOnlyBackendsCanStillBeRanked(unittest.TestCase):
    """docling emits markdown with no form feeds, so page alignment failed and `visual` was None on
    every document — the backend the harness exists to evaluate was the one it could never score."""

    def setUp(self):
        real_ex = bake.pdfmod._EXTRACTORS
        self.addCleanup(setattr, bake.pdfmod, "_EXTRACTORS", real_ex)
        bake.pdfmod._EXTRACTORS = dict(real_ex)
        bake.pdfmod._EXTRACTORS["docling"] = lambda p, l: "# Heading\n\nalpha beta gamma"  # no \f
        real_vis, real_pt = bake.visual.check_pages, bake.pdfmod.docling_page_text
        self.addCleanup(setattr, bake.visual, "check_pages", real_vis)
        self.addCleanup(setattr, bake.pdfmod, "docling_page_text", real_pt)
        self.seen = {}

        def fake(pdf, candidate_page_text, *, pages, seed):
            self.seen.update(candidate_page_text)
            return bake.visual.VisualReport(
                seed=seed, sampled=pages,
                checks=(bake.visual.PageCheck(1, 10, 9, order=1.0),))
        bake.visual.check_pages = fake

    def test_structured_provenance_supplies_the_pages_markdown_lost(self):
        bake.pdfmod.docling_page_text = lambda pdf: {1: "alpha beta gamma"}
        rep = bake.bake_off([PDFS / "single-page.pdf"], ["docling"], sample=1)
        row = rep["pdfs"]["single-page.pdf"]["tiers"]["docling"]
        self.assertIsNotNone(row["visual"], "docling was scored, not skipped")
        self.assertEqual(self.seen, {1: "alpha beta gamma"})
        self.assertEqual(rep["ranking"], ["docling"])

    def test_a_backend_that_cannot_supply_pages_is_still_reported_not_guessed_at(self):
        bake.pdfmod.docling_page_text = lambda pdf: None
        rep = bake.bake_off([PDFS / "single-page.pdf"], ["docling"], sample=1)
        row = rep["pdfs"]["single-page.pdf"]["tiers"]["docling"]
        self.assertIsNone(row["visual"])
        self.assertIn("no page separators", row["visual_note"])

    def test_an_unreachable_endpoint_is_an_unscored_row_not_a_crash(self):
        def boom(pdf):
            raise OSError("connection refused")
        bake.pdfmod.docling_page_text = boom
        rep = bake.bake_off([PDFS / "single-page.pdf"], ["docling"], sample=1)
        self.assertIsNone(rep["pdfs"]["single-page.pdf"]["tiers"]["docling"]["visual"])


class SurvivesACrashOnTheLastDocument(unittest.TestCase):
    """A real run is hours — two model backends convert every page. Holding every result in memory
    and printing once at the end meant a crash on the final document discarded the whole run."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-bakeoff-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.out = self.tmp / "report.json"
        self.pdfs = [PDFS / "single-page.pdf", PDFS / "three-pages.pdf"]
        real = bake.visual.check_pages
        self.addCleanup(setattr, bake.visual, "check_pages", real)
        bake.visual.check_pages = lambda pdf, cpt, *, pages, seed: bake.visual.VisualReport(
            seed=seed, sampled=pages,
            checks=(bake.visual.PageCheck(1, 10, 10, order=1.0),))

    def test_the_checkpoint_lands_after_every_document_not_at_the_end(self):
        seen = []
        real_cp = bake._checkpoint

        def spy(report, out):
            real_cp(report, out)
            if out is not None:
                seen.append(len(json.loads(out.read_text())["pdfs"]))
        self.addCleanup(setattr, bake, "_checkpoint", real_cp)
        bake._checkpoint = spy
        bake.bake_off(self.pdfs, ["pdftotext"], sample=1, out=self.out)
        self.assertEqual(seen[:2], [1, 2], "a partial report must exist before the run ends")

    def test_a_crash_midway_leaves_the_finished_documents_on_disk(self):
        real = bake._declared
        self.addCleanup(setattr, bake, "_declared", real)

        def boom(pdf):
            if pdf.name == "three-pages.pdf":
                raise RuntimeError("simulated crash on the second document")
            return real(pdf)
        bake._declared = boom
        with self.assertRaises(RuntimeError):
            bake.bake_off(self.pdfs, ["pdftotext"], sample=1, out=self.out)
        saved = json.loads(self.out.read_text())
        self.assertEqual(list(saved["pdfs"]), ["single-page.pdf"])   # the first survived

    def test_resume_skips_what_was_already_measured(self):
        bake.bake_off([self.pdfs[0]], ["pdftotext"], sample=1, out=self.out)
        touched = []
        real = bake._declared
        self.addCleanup(setattr, bake, "_declared", real)
        bake._declared = lambda pdf: (touched.append(pdf.name), real(pdf))[1]
        rep = bake.bake_off(self.pdfs, ["pdftotext"], sample=1, out=self.out, resume=True)
        self.assertEqual(touched, ["three-pages.pdf"], "the finished document was re-measured")
        self.assertEqual(sorted(rep["pdfs"]), ["single-page.pdf", "three-pages.pdf"])

    def test_resuming_across_a_different_seed_is_refused_not_merged(self):
        # merging two seeds silently blends two experiments into one table
        bake.bake_off([self.pdfs[0]], ["pdftotext"], sample=1, seed=1, out=self.out)
        with self.assertRaises(SystemExit):
            bake.bake_off(self.pdfs, ["pdftotext"], sample=1, seed=2, out=self.out, resume=True)
        with self.assertRaises(SystemExit):
            bake.bake_off(self.pdfs, ["pdftotext"], sample=2, seed=1, out=self.out, resume=True)

    def test_a_corrupt_checkpoint_restarts_rather_than_half_loading(self):
        self.out.write_text("{not json", encoding="utf-8")
        rep = bake.bake_off([self.pdfs[0]], ["pdftotext"], sample=1, out=self.out, resume=True)
        self.assertEqual(list(rep["pdfs"]), ["single-page.pdf"])

    def test_no_partial_file_is_left_behind(self):
        bake.bake_off(self.pdfs, ["pdftotext"], sample=1, out=self.out)
        self.assertEqual(list(self.tmp.glob("*.part")), [], "temp checkpoint was orphaned")

    def test_without_out_nothing_is_written(self):
        bake.bake_off([self.pdfs[0]], ["pdftotext"], sample=1)
        self.assertEqual(list(self.tmp.iterdir()), [])


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
