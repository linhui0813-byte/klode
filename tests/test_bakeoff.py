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
# The corpus SCORE also needs pdftoppm + tesseract (visual.py). Guarding on poppler alone meant a
# partial install ran the class and failed it instead of skipping accurately.
from klode.lib import visual as _visual                                   # noqa: E402
CAN_SCORE = HAVE_POPPLER and _visual.available()[0]


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

    # TWO documents, because a ranking over one shared document is now correctly refused — an
    # audit found the single-document version was the only case these tests ever exercised.
    DOCS = [PDFS / "single-page.pdf", PDFS / "three-pages.pdf"]

    def test_the_better_backend_ranks_first(self):
        bake.visual.check_pages = self._fake
        rep = bake.bake_off(self.DOCS, ["poor", "good"], sample=1)
        self.assertEqual(rep["ranking"], ["good", "poor"])       # NOT insertion order
        self.assertGreater(rep["aggregate"]["good"]["median_visual"],
                           rep["aggregate"]["poor"]["median_visual"])

    def test_a_backend_with_inverted_reading_order_is_demoted_below_a_worse_recall(self):
        # recall is order-blind: `reversed` matches every OCR token and outscores `poor` on recall
        # alone. Ranking must still put it last — the scrambling is the failure this harness exists
        # to catch, and it was measured and then discarded.
        bake.visual.check_pages = self._fake
        rep = bake.bake_off(self.DOCS, ["reversed", "poor"], sample=1)
        agg = rep["aggregate"]
        self.assertGreater(agg["reversed"]["median_visual"], agg["poor"]["median_visual"])
        self.assertTrue(agg["reversed"]["order_inverted"])
        self.assertEqual(rep["ranking"], ["poor", "reversed"])

    def test_an_unmeasurable_backend_sorts_last_rather_than_scoring_zero(self):
        # through the REAL bake_off. The previous version reimplemented the aggregation inline, so
        # deleting the production ranking left it green.
        bake.visual.check_pages = self._fake
        rep = bake.bake_off(self.DOCS, ["unmeasurable", "poor"], sample=1)
        self.assertIsNone(rep["aggregate"]["unmeasurable"]["median_visual"])
        # it is NOT ranked at all now: it shares no measured document with `poor`, and ordering it
        # against a backend it was never compared against was the Critical this replaces
        self.assertEqual(rep["ranking"], [])
        self.assertIn("unmeasurable", rep["unrankable"])

    def test_a_backend_measured_on_half_the_corpus_cannot_win(self):
        """The Critical two independent auditors reproduced.

        `sparse` scored 1.0 on the one document it managed and FAILED on the other; `complete`
        scored 0.9 on both. The old aggregation dropped failed pairs entirely, so `sparse` was
        reported as `scored 1/1` — indistinguishable from full coverage — and ranked first. A
        confident verdict on unmeasured evidence, in the instrument that decides the tier ladder.
        """
        bake.pdfmod._EXTRACTORS["sparse"] = lambda p, l: (
            self.GOOD if p.name == "single-page.pdf"
            else (_ for _ in ()).throw(RuntimeError("backend failed")))
        bake.visual.check_pages = self._fake
        rep = bake.bake_off(self.DOCS, ["sparse", "poor"], sample=1)

        a = rep["aggregate"]["sparse"]
        self.assertEqual((a["measured"], a["attempted"]), (1, 2), "coverage must be visible")
        self.assertEqual(a["extraction_failures"], 1)
        self.assertNotIn("sparse", rep["ranking"], "a half-measured backend was ranked")
        self.assertEqual(rep["ranking"], [], "no paired basis exists, so nothing may be ranked")
        self.assertIn("sparse", rep["unrankable"])

    def test_a_failed_pair_is_recorded_rather_than_dropped(self):
        # dropping it is what made the denominator count only successes
        bake.pdfmod._EXTRACTORS["sparse"] = lambda p, l: (
            self.GOOD if p.name == "single-page.pdf"
            else (_ for _ in ()).throw(RuntimeError("backend failed")))
        bake.visual.check_pages = self._fake
        rep = bake.bake_off(self.DOCS, ["sparse"], sample=1)
        rows = [d["tiers"]["sparse"] for d in rep["pdfs"].values()]
        self.assertEqual(len(rows), 2, "the failed document has no row at all")
        self.assertTrue(any(r.get("extraction_error") for r in rows))

    def test_an_unavailable_third_backend_does_not_suppress_the_others(self):
        """A REGRESSION my own fix introduced, caught by an independent verification.

        Intersecting the paired set over every REQUESTED tier meant `--tiers a,b,marker` with
        marker not installed emptied the set and refused a perfectly good a-vs-b comparison. The
        fix for a fail-open had become a fail-closed — a different way of returning the wrong
        answer, and one my own tests did not cover because they never used three backends.
        """
        bake.pdfmod._EXTRACTORS["absent"] = lambda p, l: (_ for _ in ()).throw(
            ImportError("not installed"))
        bake.visual.check_pages = self._fake
        rep = bake.bake_off(self.DOCS, ["poor", "good", "absent"], sample=1)
        self.assertEqual(rep["ranking"], ["good", "poor"])
        self.assertIn("absent", rep["unrankable"])

    def test_a_half_measured_backend_is_dropped_not_allowed_to_shrink_the_basis(self):
        # the subtler form of the same over-correction: a partially-measured third backend
        # reduced the shared set below the threshold and suppressed the comparison
        bake.pdfmod._EXTRACTORS["half"] = lambda p, l: (
            self.GOOD if p.name == "single-page.pdf"
            else (_ for _ in ()).throw(RuntimeError("boom")))
        bake.visual.check_pages = self._fake
        rep = bake.bake_off(self.DOCS, ["poor", "good", "half"], sample=1)
        self.assertEqual(rep["ranking"], ["good", "poor"])
        self.assertIn("half", rep["unrankable"])
        self.assertIn("too few shared", rep["unrankable"]["half"])

    def test_full_coverage_is_still_ranked_normally(self):
        # the fix must not refuse a legitimate comparison — that would be a different fail-closed
        bake.visual.check_pages = self._fake
        rep = bake.bake_off(self.DOCS, ["poor", "good"], sample=1)
        self.assertEqual(rep["ranking"], ["good", "poor"])
        self.assertEqual(rep["ranking_note"], "")
        self.assertEqual(len(rep["paired_documents"]), 2)

    def test_the_worst_document_is_reported_beside_the_median(self):
        """A median over pages and then over documents lets a backend wreck almost half of both and
        still score perfectly. The tail has to survive the aggregation to be actionable."""
        bake.pdfmod._EXTRACTORS["mixed"] = lambda p, l: (
            self.GOOD if p.name == "single-page.pdf" else self.POOR)
        bake.visual.check_pages = self._fake
        rep = bake.bake_off(self.DOCS, ["mixed", "good"], sample=1)
        a = rep["aggregate"]["mixed"]
        self.assertEqual(a["worst_visual"], 0.2)              # the bad document is visible
        self.assertEqual(a["scores"], [0.2, 1.0])             # and so is the distribution
        self.assertGreater(a["median_visual"], a["worst_visual"])

    def test_each_document_gets_its_own_sampled_pages(self):
        # one global seed gave every equal-length document identical page positions, so a common
        # structure was systematically included or excluded across the whole corpus
        seeds = {bake._doc_seed(1618, p) for p in self.DOCS}
        self.assertEqual(len(seeds), len(self.DOCS), "two documents share a sampling seed")
        # ...and it stays reproducible from (seed, document) alone
        self.assertEqual(bake._doc_seed(1618, self.DOCS[0]), bake._doc_seed(1618, self.DOCS[0]))
        self.assertNotEqual(bake._doc_seed(1618, self.DOCS[0]), bake._doc_seed(99, self.DOCS[0]))

    def test_the_control_is_not_extracted_twice(self):
        # pdftotext is the control AND a candidate tier; it ran as both, paying for the same
        # subprocess twice per document
        calls = []
        real = bake.pdfmod._EXTRACTORS["pdftotext"]
        bake.pdfmod._EXTRACTORS["pdftotext"] = lambda p, l: (calls.append(p.name), real(p, l))[1]
        bake.visual.check_pages = self._fake
        bake.bake_off([PDFS / "single-page.pdf"], ["pdftotext"], sample=1)
        self.assertEqual(len(calls), 1, f"the control ran {len(calls)} times")

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
        row = rep["pdfs"][bake._doc_id(PDFS / "single-page.pdf")]["tiers"]["docling"]
        self.assertIsNotNone(row["visual"], "docling was scored, not skipped")
        self.assertEqual(self.seen, {1: "alpha beta gamma"})
        # one backend is not a ranking — what matters is that docling was SCORED at all, which it
        # never was before its structured page text was read
        self.assertEqual(rep["aggregate"]["docling"]["measured"], 1)

    def test_the_backend_is_converted_once_per_document_not_twice(self):
        """Both audits found this: `_extract` converts for the text, `_structured_pages` converted
        AGAIN for the page text. That doubles the most expensive work — marker is minutes per
        document — and the two runs are separate nondeterministic executions, so `words` could
        describe a different conversion than `visual`."""
        calls = []
        bake.pdfmod.docling_page_text = lambda pdf: (calls.append(pdf.name), {1: "alpha beta gamma"})[1]
        bake.bake_off([PDFS / "single-page.pdf", PDFS / "three-pages.pdf"], ["docling"], sample=1)
        self.assertEqual(len(calls), len(set(calls)),
                         f"a document was converted more than once: {calls}")

    def test_a_backend_that_cannot_supply_pages_is_still_reported_not_guessed_at(self):
        bake.pdfmod.docling_page_text = lambda pdf: None
        rep = bake.bake_off([PDFS / "single-page.pdf"], ["docling"], sample=1)
        row = rep["pdfs"][bake._doc_id(PDFS / "single-page.pdf")]["tiers"]["docling"]
        self.assertIsNone(row["visual"])
        self.assertIn("no page separators", row["visual_note"])

    def test_an_unreachable_endpoint_is_an_unscored_row_not_a_crash(self):
        # `visual is None` alone passed even with the structured-page lookup deleted entirely, and
        # the row then blamed "no page separators" for what was actually an endpoint failure
        called = []

        def boom(pdf):
            called.append(pdf.name)
            raise OSError("connection refused")
        bake.pdfmod.docling_page_text = boom
        rep = bake.bake_off([PDFS / "single-page.pdf"], ["docling"], sample=1)
        row = rep["pdfs"][bake._doc_id(PDFS / "single-page.pdf")]["tiers"]["docling"]
        self.assertEqual(called, ["single-page.pdf"], "the lookup was never attempted")
        self.assertIsNone(row["visual"])
        self.assertIn("visual_note", row)


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
        self.assertEqual([d["name"] for d in saved["pdfs"].values()], ["single-page.pdf"])   # the first survived

    def test_resume_continues_the_same_experiment_after_a_crash(self):
        # the real use case: the SAME inputs, interrupted partway. (A different input list is a
        # different experiment and is refused — see the test above.)
        real = bake._declared
        self.addCleanup(setattr, bake, "_declared", real)
        bake._declared = lambda pdf: (_ for _ in ()).throw(RuntimeError("crash")) \
            if pdf.name == "three-pages.pdf" else real(pdf)
        with self.assertRaises(RuntimeError):
            bake.bake_off(self.pdfs, ["pdftotext"], sample=1, out=self.out)

        touched = []
        bake._declared = lambda pdf: (touched.append(pdf.name), real(pdf))[1]
        rep = bake.bake_off(self.pdfs, ["pdftotext"], sample=1, out=self.out, resume=True)
        self.assertEqual(touched, ["three-pages.pdf"], "the finished document was re-measured")
        self.assertEqual(sorted(d["name"] for d in rep["pdfs"].values()),
                         ["single-page.pdf", "three-pages.pdf"])

    def test_resuming_across_a_different_seed_is_refused_not_merged(self):
        # merging two seeds silently blends two experiments into one table
        bake.bake_off([self.pdfs[0]], ["pdftotext"], sample=1, seed=1, out=self.out)
        with self.assertRaises(SystemExit):
            bake.bake_off(self.pdfs, ["pdftotext"], sample=1, seed=2, out=self.out, resume=True)
        with self.assertRaises(SystemExit):
            bake.bake_off(self.pdfs, ["pdftotext"], sample=2, seed=1, out=self.out, resume=True)

    def test_a_corrupt_checkpoint_stops_the_run_and_is_not_overwritten(self):
        # The previous version BLESSED a silent restart. An audit called that what it is: `--resume`
        # exists to preserve hours of measurement, and silently starting fresh then checkpointing
        # over the damaged file destroys the only evidence of what had been measured.
        self.out.write_text("{not json", encoding="utf-8")
        with self.assertRaises(SystemExit) as e:
            bake.bake_off(self.pdfs, ["pdftotext"], sample=1, out=self.out, resume=True)
        self.assertIn("unreadable", str(e.exception))
        self.assertEqual(self.out.read_text(), "{not json", "the damaged checkpoint was overwritten")

    def test_resume_without_a_checkpoint_stops_rather_than_starting_over(self):
        missing = self.tmp / "nope.json"
        with self.assertRaises(SystemExit):
            bake.bake_off(self.pdfs, ["pdftotext"], sample=1, out=missing, resume=True)
        self.assertFalse(missing.exists())

    def test_resume_refuses_a_checkpoint_from_a_different_experiment(self):
        # seed and sample were the only things compared; adding a tier, editing a PDF, or changing
        # the anchors merged old measurements with new and reported a ranking for a tier that had
        # never been run against those documents.
        bake.bake_off(self.pdfs, ["pdftotext"], sample=1, out=self.out)
        for label, kw in (("an added tier", {"tiers": ["pdftotext", "good"]}),
                          ("a different seed", {"seed": 99}),
                          ("a different sample size", {"sample": 2}),
                          ("a different document set", {"docs": self.pdfs[:1]}),
                          ("changed anchors", {"anchors": {"single-page": ["x"]}})):
            with self.subTest(label):
                with self.assertRaises(SystemExit) as e:
                    bake.bake_off(kw.pop("docs", self.pdfs), kw.pop("tiers", ["pdftotext"]),
                                  sample=kw.pop("sample", 1), seed=kw.pop("seed", 1618),
                                  out=self.out, resume=True, **kw)
                self.assertIn("different experiment", str(e.exception))

    def test_an_edited_input_pdf_invalidates_the_checkpoint(self):
        # identity by CONTENT: a path can be replaced under the same name between runs
        copy = self.tmp / "single-page.pdf"
        copy.write_bytes((PDFS / "single-page.pdf").read_bytes())
        bake.bake_off([copy], ["pdftotext"], sample=1, out=self.out)
        copy.write_bytes((PDFS / "three-pages.pdf").read_bytes())
        with self.assertRaises(SystemExit) as e:
            bake.bake_off([copy], ["pdftotext"], sample=1, out=self.out, resume=True)
        self.assertIn("different experiment", str(e.exception))

    def test_two_pdfs_sharing_a_basename_are_not_collapsed(self):
        # keyed by `pdf.name`, `a/report.pdf` and `b/report.pdf` overwrote each other, and on
        # resume the second was skipped as already done
        a, b = self.tmp / "a", self.tmp / "b"
        for d in (a, b):
            d.mkdir()
            (d / "report.pdf").write_bytes((PDFS / "single-page.pdf").read_bytes())
        rep = bake.bake_off([a / "report.pdf", b / "report.pdf"], ["pdftotext"], sample=1)
        self.assertEqual(len(rep["pdfs"]), 2, "two distinct documents collapsed into one row")

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
        self.assertIn("fakegood", rep["pdfs"][bake._doc_id(PDFS / "single-page.pdf")]["tiers"])

    def test_an_unknown_tier_is_reported(self):
        rep = bake.bake_off([PDFS / "single-page.pdf"], ["nonesuch"], sample=1)
        self.assertIn("nonesuch", rep["skipped"])

    def test_the_cli_refuses_an_unknown_tier_and_a_non_pdf(self):
        # both produced a confident empty report and exit 0: "measured, nothing to report"
        # is indistinguishable from "never ran"
        for argv, why in (
                (["--pdfs", str(PDFS), "--tiers", "nonesuch"], "unknown tier"),
                (["--pdfs", str(REPO / "README.md"), "--tiers", "pdftotext"], "not a PDF"),
                (["--pdfs", str(PDFS), "--tiers", "pdftotext", "--sample", "0"], "sample < 1"),
                (["--pdfs", str(PDFS), "--tiers", "pdftotext,pdftotext"], "repeated tier"),
                (["--pdfs", str(PDFS / "nope"), "--tiers", "pdftotext"], "missing path")):
            with self.subTest(why):
                with self.assertRaises(SystemExit):
                    bake.main(argv)

    def test_the_seed_and_sampled_pages_are_recorded(self):
        # asserted UNCONDITIONALLY via a mocked visual report: the previous version guarded the
        # assertion behind `if visual is not None`, so in exactly the environment that needed
        # checking (no render tooling) it executed nothing and passed.
        real = bake.visual.check_pages
        seen = {}

        def capture(pdf, candidate_page_text, *, pages, seed):
            seen.update(pages=pages, seed=seed)
            return bake.visual.VisualReport(
                seed=seed, sampled=pages,
                checks=(bake.visual.PageCheck(page=1, ocr_tokens=10, matched=10),))
        bake.visual.check_pages = capture
        self.addCleanup(setattr, bake.visual, "check_pages", real)
        rep = bake.bake_off([PDFS / "three-pages.pdf"], ["pdftotext"], sample=2, seed=99)
        self.assertEqual(rep["seed"], 99)
        row = rep["pdfs"][bake._doc_id(PDFS / "three-pages.pdf")]["tiers"]["pdftotext"]
        # the EXACT seed and pages measurement received, not just the count. Checking only
        # `len(...) == 2` passed with any two fabricated page numbers, and checking the echoed
        # top-level seed passed even if measurement used a different one.
        want_seed = bake._doc_seed(99, PDFS / "three-pages.pdf")
        self.assertEqual(seen["seed"], want_seed)
        self.assertEqual(tuple(row["visual_sampled"]),
                         bake.visual.sample_pages(3, 2, want_seed))
        self.assertEqual(tuple(seen["pages"]), tuple(row["visual_sampled"]))
        self.assertIsNotNone(row["visual"])

    def test_a_run_that_could_not_rank_exits_nonzero(self):
        # both modes returned 0 even when `ranking` was empty, so a caller could not tell
        # "these are ordered" from "nothing could be compared"
        import io as _io
        from contextlib import redirect_stdout as _rs
        for extra in ([], ["--json"]):
            with self.subTest(json=bool(extra)):
                buf = _io.StringIO()
                with _rs(buf):
                    rc = bake.main(["--pdfs", str(PDFS / "single-page.pdf"),
                                    "--tiers", "pdftotext", "--sample", "1"] + extra)
                self.assertEqual(rc, bake.EXIT_NOT_RANKED)

    def test_the_report_is_json_serialisable(self):
        rep = bake.bake_off([PDFS / "single-page.pdf"], ["pdftotext"], sample=1)
        json.dumps(rep)          # a report nobody can persist is not a measurement


@unittest.skipUnless(CAN_SCORE, "needs poppler + pdftoppm + tesseract to score")
class AgainstTheCorpus(unittest.TestCase):
    def test_it_runs_and_scores_the_corpus(self):
        pdfs = sorted(PDFS.glob("*.pdf"))
        rep = bake.bake_off(pdfs, ["pdftotext"], sample=1)
        self.assertEqual(len(rep["pdfs"]), len(pdfs))
        # EVERY document, not "at least one": asserting `scored` is truthy still passed with
        # scoring broken for four of five
        scored = {d["name"]: d["tiers"]["pdftotext"].get("visual") for d in rep["pdfs"].values()}
        self.assertEqual(sorted(scored), sorted(p.name for p in pdfs))
        unscored = [n for n, v in scored.items() if v is None]
        self.assertEqual(unscored, [], f"unscored: {unscored}")
        self.assertEqual(rep["skipped"], {})
        self.assertGreater(min(scored.values()), 0.9)  # the control is faithful on a clean corpus
        self.assertEqual(rep["aggregate"]["pdftotext"]["measured"], len(pdfs))

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
