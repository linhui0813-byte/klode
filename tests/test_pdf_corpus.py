"""WI-0 — the labeled PDF corpus is real, reproducible, and says what it does not cover.

Ground truth here is true *by construction*: the generator places known text at known coordinates on
a known page, so nothing depends on an extractor to say what the page contains. That is the whole
point — a corpus labeled by another extractor would beg the question the plan exists to settle.

Structural assertions run everywhere. The ones that need poppler skip with a reason rather than
failing, because CI installs nothing (the suite is stdlib-only by design).
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

PDFS = REPO / "tests" / "fixtures" / "pdfs"
TRUTH = json.loads((PDFS / "GROUND-TRUTH.json").read_text(encoding="utf-8"))
HAVE_POPPLER = bool(shutil.which("pdfinfo") and shutil.which("pdftotext"))


def _pdfinfo_pages(pdf: Path) -> int:
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, timeout=30)
    for line in out.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split()[1])
    raise AssertionError(f"pdfinfo reported no page count for {pdf}")


class CorpusExists(unittest.TestCase):
    def test_every_declared_file_is_present(self):
        # the plan's own check was `find tests/fixtures -iname '*.pdf'` returning 0
        for name in TRUTH["files"]:
            self.assertTrue((PDFS / name).is_file(), f"{name} declared but absent")
        self.assertTrue(list(PDFS.glob("*.pdf")), "no PDFs in the corpus at all")

    def test_every_present_file_is_declared(self):
        # a PDF with no ground truth is worse than no PDF: it invites use as if it were labeled
        for p in PDFS.glob("*.pdf"):
            self.assertIn(p.name, TRUTH["files"], f"{p.name} has no ground-truth entry")

    def test_the_corpus_states_what_it_does_not_cover(self):
        # an evaluation set that does not name its gaps will be mistaken for a complete one
        self.assertTrue(TRUTH["not_covered"])
        joined = " ".join(TRUTH["not_covered"]).lower()
        for gap in ("scan", "broken text layer", "script"):
            self.assertIn(gap, joined)

    def test_regeneration_is_byte_identical(self):
        # the corpus must be reproducible from the generator, or "ground truth" is just some bytes
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for p in PDFS.glob("*.pdf"):
            shutil.copy2(p, tmp / p.name)
        subprocess.run([sys.executable, str(PDFS / "make_fixtures.py")], check=True,
                       capture_output=True, timeout=60)
        for p in sorted(PDFS.glob("*.pdf")):
            self.assertEqual(p.read_bytes(), (tmp / p.name).read_bytes(),
                             f"{p.name} changed on regeneration — the generator is not deterministic")


@unittest.skipUnless(HAVE_POPPLER, "poppler (pdfinfo/pdftotext) not installed")
class CorpusMatchesGroundTruth(unittest.TestCase):
    def test_page_counts_match(self):
        for name, meta in TRUTH["files"].items():
            with self.subTest(pdf=name):
                self.assertEqual(_pdfinfo_pages(PDFS / name), meta["pages"])

    def test_declared_text_actually_extracts(self):
        for name, meta in TRUTH["files"].items():
            with self.subTest(pdf=name):
                out = subprocess.run(["pdftotext", "-layout", str(PDFS / name), "-"],
                                     capture_output=True, text=True, timeout=60).stdout
                # every page's ground-truth tokens must appear; this checks the fixture, not an
                # extractor's ordering — ordering is what the agreement metrics measure later
                for page in meta["text"]:
                    lines = (page["left"] + page["right"]) if isinstance(page, dict) else page
                    for line in lines:
                        self.assertIn(line.split()[-1], out,
                                      f"{name}: unique token from {line[:30]!r} missing")

    def test_the_blank_page_really_is_blank(self):
        meta = TRUTH["files"]["blank-middle.pdf"]
        out = subprocess.run(["pdftotext", "-layout", str(PDFS / "blank-middle.pdf"), "-"],
                             capture_output=True, text=True, timeout=60).stdout
        pages = out.split("\f")
        if pages and not pages[-1].strip():
            pages = pages[:-1]                     # drop the trailing form-feed artefact
        self.assertEqual(len(pages), meta["pages"])
        for n in meta["empty_pages"]:
            self.assertEqual(pages[n - 1].split(), [], f"page {n} should be empty")

    def test_a_trailing_form_feed_does_not_inflate_the_page_count(self):
        # the off-by-one an audit predicted: "A\fB\fC\f".split("\f") is 4 elements for 3 pages
        for name in ("single-page.pdf", "three-pages.pdf"):
            with self.subTest(pdf=name):
                out = subprocess.run(["pdftotext", "-layout", str(PDFS / name), "-"],
                                     capture_output=True, text=True, timeout=60).stdout
                raw = out.split("\f")
                self.assertEqual(len(raw), TRUTH["files"][name]["pages"] + 1,
                                 "expected exactly one trailing empty segment")
                self.assertEqual(raw[-1].strip(), "")


if __name__ == "__main__":
    unittest.main()
