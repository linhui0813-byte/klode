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
    def test_the_source_page_map_is_not_content_coverage(self):
        # This test previously ASSERTED the bug: it blessed docling's top-level `pages` map as
        # evidence of extracted content. That map is the SOURCE's page inventory — a document
        # declaring pages 1-3 whose blocks carry provenance for only 1 and 3 has lost page 2, and
        # reading the map would report full coverage and mask exactly that.
        masked = {"pages": {"1": {}, "2": {}, "3": {}},
                  "texts": [{"prov": [{"page_no": 1}]}, {"prov": [{"page_no": 3}]}]}
        self.assertEqual(coverage.pages_from_docling(masked), (1, 3))
        cov = coverage.assess(3, "a\fb\fc\f", coverage.pages_from_docling(masked))
        self.assertEqual(cov.candidate_missing, (2,))     # the dropped page is visible

    def test_a_page_map_with_no_content_provenance_says_it_cannot_tell(self):
        self.assertIsNone(coverage.pages_from_docling({"pages": {"1": {}, "2": {}}}))

    def test_prov_page_no_shape(self):
        doc = {"texts": [{"prov": [{"page_no": 1}]}, {"prov": [{"page_no": 3}]}],
               "tables": [{"prov": [{"page_no": 2}]}]}
        self.assertEqual(coverage.pages_from_docling(doc), (1, 2, 3))

    def test_cannot_say_is_None_not_empty(self):
        # the distinction that matters: "no provenance" must never read as "nothing missing"
        for doc in (None, {}, {"texts": []}, "not a dict", {"pages": "nonsense"}):
            with self.subTest(doc=doc):
                self.assertIsNone(coverage.pages_from_docling(doc))

    def test_malformed_provenance_is_ignored_not_crashed_on(self):
        doc = {"texts": [{"prov": [{"page_no": True}]},     # bool is not a page number
                         {"prov": [{"page_no": "2"}]},      # nor is a string
                         {"prov": 1},                       # `prov: 1` used to raise TypeError
                         {"prov": "junk"},                  # a str is iterable — used to slip through
                         "junk",
                         {"prov": [{"page_no": 5}]}]}       # the only real one
        self.assertEqual(coverage.pages_from_docling(doc), (5,))


class PageTextFromStructure(unittest.TestCase):
    """Markdown has no form feeds, so a markdown-only backend scored `visual=None` on every
    document — docling, the backend the bake-off exists to evaluate, could never be ranked at all.
    The structured result carries the page boundary the markdown lost."""

    def test_blocks_are_grouped_under_their_declared_page(self):
        doc = {"texts": [{"text": "alpha", "prov": [{"page_no": 1}]},
                         {"text": "beta", "prov": [{"page_no": 2}]},
                         {"text": "gamma", "prov": [{"page_no": 1}]}]}
        self.assertEqual(coverage.page_text_from_docling(doc),
                         {1: "alpha\ngamma", 2: "beta"})

    def test_document_order_is_preserved_within_a_page(self):
        # sorting the blocks would destroy the reading-order signal the visual check measures
        doc = {"texts": [{"text": "zulu", "prov": [{"page_no": 1}]},
                         {"text": "alpha", "prov": [{"page_no": 1}]}]}
        self.assertEqual(coverage.page_text_from_docling(doc)[1], "zulu\nalpha")

    def test_tables_and_pictures_contribute_their_text(self):
        doc = {"tables": [{"text": "| a | b |", "prov": [{"page_no": 2}]}],
               "pictures": [{"text": "caption", "prov": [{"page_no": 2}]}]}
        self.assertEqual(coverage.page_text_from_docling(doc), {2: "| a | b |\ncaption"})

    def test_a_block_claiming_two_pages_is_counted_once(self):
        doc = {"texts": [{"text": "spanning", "prov": [{"page_no": 1}, {"page_no": 2}]}]}
        self.assertEqual(coverage.page_text_from_docling(doc), {1: "spanning"})

    def test_cannot_say_is_None_not_an_empty_map(self):
        for doc in (None, {}, "not a dict", {"texts": []},
                    {"texts": [{"text": "x"}]},                  # no provenance
                    {"texts": [{"prov": [{"page_no": 1}]}]},     # no text
                    {"texts": [{"text": "   ", "prov": [{"page_no": 1}]}]}):
            with self.subTest(doc=doc):
                self.assertIsNone(coverage.page_text_from_docling(doc))

    def test_malformed_input_is_ignored_not_crashed_on(self):
        doc = {"texts": [{"text": 7, "prov": [{"page_no": 1}]},
                         {"text": "ok", "prov": "junk"},
                         "junk",
                         {"text": "real", "prov": [{"page_no": 4}]}]}
        self.assertEqual(coverage.page_text_from_docling(doc), {4: "real"})


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
