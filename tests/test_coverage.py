"""WI-2 — page coverage, and the distinction an earlier design collapsed.

Control coverage is NOT candidate coverage. Counting `pdfinfo` pages against the control's form
feeds produces a number that is byte-identical whether the candidate kept every page or dropped
half, because the candidate never appears in it. These tests pin that separation, and pin `None`
(cannot say) as distinct from `()` (nothing missing).
"""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from klode.lib import coverage                                            # noqa: E402
from klode.lib.formats._base import Extraction                            # noqa: E402

PDFS = REPO / "tests" / "fixtures" / "pdfs"
TRUTH = json.loads((PDFS / "GROUND-TRUTH.json").read_text(encoding="utf-8"))
HAVE_POPPLER = bool(shutil.which("pdfinfo") and shutil.which("pdftotext"))


class PageSplitting(unittest.TestCase):
    def test_the_trailing_form_feed_does_not_add_a_page(self):
        # "A\fB\fC\f".split("\f") is 4 elements for 3 pages — the off-by-one an audit predicted
        self.assertEqual(coverage.split_pages("A\fB\fC\f"), ["A", "B", "C"])
        self.assertEqual(coverage.split_pages("A\f"), ["A"])
        self.assertEqual(coverage.split_pages("A"), ["A"])

    def test_a_genuinely_blank_final_page_is_still_a_page(self):
        # only ONE trailing empty segment is dropped: "A\fB\f\f" is 3 pages, the last one blank
        self.assertEqual(coverage.split_pages("A\fB\f\f"), ["A", "B", ""])

    def test_empty_input(self):
        self.assertEqual(coverage.split_pages(""), [""])


class DoclingProvenance(unittest.TestCase):
    def test_pages_map_shape(self):
        doc = {"pages": {"1": {}, "2": {}, "3": {}}}
        self.assertEqual(coverage.pages_from_docling(doc), (1, 2, 3))

    def test_prov_page_no_shape(self):
        doc = {"texts": [{"prov": [{"page_no": 1}]}, {"prov": [{"page_no": 3}]}],
               "tables": [{"prov": [{"page_no": 2}]}]}
        self.assertEqual(coverage.pages_from_docling(doc), (1, 2, 3))

    def test_cannot_say_is_None_not_empty(self):
        # the distinction that matters: "no provenance" must never read as "nothing missing"
        for doc in (None, {}, {"texts": []}, "not a dict", {"pages": "nonsense"}):
            with self.subTest(doc=doc):
                self.assertIsNone(coverage.pages_from_docling(doc))

    def test_malformed_page_numbers_are_ignored_not_crashed_on(self):
        doc = {"pages": {"1": {}, "notanumber": {}}, "texts": [{"prov": [{"page_no": True}]},
                                                               {"prov": [{"page_no": "2"}]},
                                                               {"prov": "junk"}, "junk"]}
        self.assertEqual(coverage.pages_from_docling(doc), (1,))   # bool/str rejected, no exception


class ControlVersusCandidate(unittest.TestCase):
    """The defect this module exists to prevent."""

    def test_control_coverage_is_identical_whether_the_candidate_dropped_pages(self):
        control = "page one words\fpage two words\fpage three words\f"
        kept = coverage.assess(3, control, candidate_pages=(1, 2, 3))
        dropped = coverage.assess(3, control, candidate_pages=(1, 3))
        # the CONTROL view cannot tell these apart — that was the whole bug
        self.assertEqual((kept.control_pages, kept.control_empty),
                         (dropped.control_pages, dropped.control_empty))
        # the CANDIDATE view can
        self.assertEqual(kept.candidate_missing, ())
        self.assertEqual(dropped.candidate_missing, (2,))

    def test_unknown_candidate_coverage_does_not_masquerade_as_complete(self):
        c = coverage.assess(3, "a\fb\fc\f", candidate_pages=None)
        self.assertFalse(c.candidate_known)
        self.assertEqual(c.candidate_missing, ())      # empty, but ONLY because it cannot say
        # a caller must gate on candidate_known before reading candidate_missing
        known = coverage.assess(3, "a\fb\fc\f", candidate_pages=(1, 2, 3))
        self.assertTrue(known.candidate_known)

    def test_a_blank_control_page_is_reported(self):
        c = coverage.assess(3, "words here\f\fmore words\f")
        self.assertEqual(c.control_empty, (2,))

    def test_control_disagreeing_with_pdfinfo_is_flagged(self):
        self.assertTrue(coverage.assess(5, "a\fb\f").control_mismatch)
        self.assertFalse(coverage.assess(2, "a\fb\f").control_mismatch)

    def test_missing_pdfinfo_is_reported_not_assumed_full(self):
        c = coverage.assess(0, "a\fb\f", candidate_pages=(1,))
        self.assertEqual(c.declared, 0)
        self.assertFalse(c.control_mismatch)            # nothing to compare against
        self.assertEqual(c.candidate_missing, ())       # cannot compute without a declared count


class ExtractionCarriesPages(unittest.TestCase):
    def test_pages_defaults_to_unknown(self):
        e = Extraction(text="x", handler="pdftotext", format="pdf")
        self.assertIsNone(e.pages)                      # a text-only backend cannot say

    def test_pages_can_be_carried(self):
        e = Extraction(text="x", handler="docling", format="pdf", pages=(1, 2))
        self.assertEqual(e.pages, (1, 2))


@unittest.skipUnless(HAVE_POPPLER, "poppler not installed")
class AgainstTheRealCorpus(unittest.TestCase):
    def _control(self, name):
        return subprocess.run(["pdftotext", "-layout", str(PDFS / name), "-"],
                              capture_output=True, text=True, timeout=60).stdout

    def _declared(self, name):
        out = subprocess.run(["pdfinfo", str(PDFS / name)], capture_output=True, text=True,
                             timeout=30).stdout
        return int(next(l.split()[1] for l in out.splitlines() if l.startswith("Pages:")))

    def test_page_counts_agree_on_every_fixture(self):
        for name, meta in TRUTH["files"].items():
            with self.subTest(pdf=name):
                c = coverage.assess(self._declared(name), self._control(name))
                self.assertEqual(c.declared, meta["pages"])
                self.assertEqual(c.control_pages, meta["pages"])
                self.assertFalse(c.control_mismatch)

    def test_the_blank_page_fixture_reports_its_blank_page(self):
        name = "blank-middle.pdf"
        c = coverage.assess(self._declared(name), self._control(name))
        self.assertEqual(c.control_empty, tuple(TRUTH["files"][name]["empty_pages"]))


if __name__ == "__main__":
    unittest.main()
