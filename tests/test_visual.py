"""WI-3 — sampled visual ground truth: the one signal that is evidence of fidelity.

Agreement compares two extractors and can only ever say "these differ". This renders the page and
reads what is actually on it, which is downstream of no extractor. The sample must be reproducible
(seed + page numbers recorded) or a spot check is an anecdote.

Tests that need poppler + tesseract skip with a reason; the pure logic always runs.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from klode.lib import coverage, visual                                    # noqa: E402

PDFS = REPO / "tests" / "fixtures" / "pdfs"
TRUTH = json.loads((PDFS / "GROUND-TRUTH.json").read_text(encoding="utf-8"))
HAVE, WHY = visual.available()


def _control_pages(pdf: Path) -> dict[int, str]:
    out = subprocess.run(["pdftotext", "-layout", str(pdf), "-"],
                         capture_output=True, text=True, timeout=60).stdout
    return {i: t for i, t in enumerate(coverage.split_pages(out), start=1)}


class Sampling(unittest.TestCase):
    """A sample nobody can reproduce is an anecdote."""

    def test_the_same_seed_yields_the_same_pages(self):
        a = visual.sample_pages(300, 5, seed=7)
        self.assertEqual(a, visual.sample_pages(300, 5, seed=7))
        self.assertEqual(len(a), 5)
        self.assertEqual(sorted(a), list(a))                 # reported in page order

    def test_different_seeds_generally_differ(self):
        self.assertNotEqual(visual.sample_pages(300, 5, seed=1),
                            visual.sample_pages(300, 5, seed=2))

    def test_asking_for_more_pages_than_exist_returns_all_of_them(self):
        self.assertEqual(visual.sample_pages(3, 10, seed=1), (1, 2, 3))

    def test_degenerate_inputs_yield_nothing(self):
        self.assertEqual(visual.sample_pages(0, 5, seed=1), ())
        self.assertEqual(visual.sample_pages(10, 0, seed=1), ())
        self.assertEqual(visual.sample_pages(-3, 5, seed=1), ())

    def test_pages_are_within_range(self):
        for p in visual.sample_pages(12, 6, seed=99):
            self.assertTrue(1 <= p <= 12)


class Abstention(unittest.TestCase):
    def test_a_missing_pdf_is_skipped_loudly(self):
        # `available()` is checked FIRST, so without pdftoppm/tesseract this asserted the toolchain
        # message instead of the behaviour under test — green on a developer machine, red in CI
        from unittest import mock
        with mock.patch.object(visual, "available", return_value=(True, "")):
            r = visual.check_pages(Path("/nonexistent.pdf"), {}, pages=(1,), seed=1)
        self.assertFalse(r.ran)
        self.assertIn("no such pdf", r.skipped)
        self.assertEqual(r.recalls, ())

    def test_a_report_that_did_not_run_has_no_quantile(self):
        r = visual.VisualReport(seed=1, sampled=(1,), checks=(), skipped="missing tesseract")
        self.assertFalse(r.ran)
        self.assertIsNone(r.quantile(0.5))
        self.assertIsNone(r.worst)

    def test_an_errored_page_scores_None_not_zero(self):
        # "no page-level text" and "wrong page-level text" are different findings; collapsing the
        # first into a 0.0 recall would invent a failure that was never measured
        c = visual.PageCheck(page=2, ocr_tokens=50, matched=0, error="candidate has no text")
        self.assertIsNone(c.recall)

    def test_an_empty_page_scores_None_rather_than_dividing_by_zero(self):
        self.assertIsNone(visual.PageCheck(page=1, ocr_tokens=0, matched=0).recall)

    def test_recall_is_computed_over_what_the_page_shows(self):
        self.assertAlmostEqual(visual.PageCheck(page=1, ocr_tokens=100, matched=75).recall, 0.75)


@unittest.skipUnless(HAVE, f"render/OCR unavailable: {WHY}")
class AgainstTheRealCorpus(unittest.TestCase):
    def test_a_faithful_candidate_scores_high_recall(self):
        pdf = PDFS / "three-pages.pdf"
        r = visual.check_pages(pdf, _control_pages(pdf), pages=(1, 2, 3), seed=42)
        self.assertTrue(r.ran)
        self.assertEqual(len(r.checks), 3)
        self.assertGreater(r.quantile(0.5), 0.95)
        self.assertEqual(r.sampled, (1, 2, 3))               # provenance of the sample is kept

    def test_a_candidate_missing_a_page_is_reported_as_an_error_not_a_score(self):
        pdf = PDFS / "three-pages.pdf"
        pages = _control_pages(pdf)
        del pages[2]                                          # candidate dropped page 2
        r = visual.check_pages(pdf, pages, pages=(1, 2, 3), seed=42)
        errored = [c for c in r.checks if c.error]
        self.assertEqual([c.page for c in errored], [2])
        self.assertIn("no text for this page", errored[0].error)
        self.assertIsNone(errored[0].recall)

    def test_a_candidate_with_the_wrong_text_scores_low_recall(self):
        # this is the case agreement-with-a-control cannot settle: the rendered page is the arbiter
        pdf = PDFS / "three-pages.pdf"
        wrong = {1: "completely unrelated wording about entirely different subject matter"}
        r = visual.check_pages(pdf, wrong, pages=(1,), seed=42)
        self.assertTrue(r.ran)
        self.assertLess(r.checks[0].recall, 0.3)

    def test_the_worst_page_is_surfaced_not_averaged_away(self):
        pdf = PDFS / "three-pages.pdf"
        pages = _control_pages(pdf)
        pages[3] = "nothing whatsoever to do with the rendered page"
        r = visual.check_pages(pdf, pages, pages=(1, 2, 3), seed=42)
        self.assertEqual(r.worst.page, 3)
        self.assertGreater(r.quantile(0.5), 0.9)             # the median page is still fine

    def test_the_blank_page_fixture_ocrs_to_nothing(self):
        pdf = PDFS / "blank-middle.pdf"
        r = visual.check_pages(pdf, _control_pages(pdf), pages=(2,), seed=1)
        self.assertEqual(r.checks[0].ocr_tokens, 0)
        self.assertIsNone(r.checks[0].recall)                 # nothing to be faithful to


if __name__ == "__main__":
    unittest.main()
