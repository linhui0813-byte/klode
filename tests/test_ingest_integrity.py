"""WI-4 + WI-5 — verification states, transactional semantics, and provenance bound to the bytes.

The plan's acceptance criterion for WI-4 is a single sentence and it is absolute: *either* success
plus a promoted artifact, *or* failure plus no shelf mutation — **never both**. v1 proposed
"exit non-zero but still write", which is neither, and whose retry would hit an existing file and
demand `--force`.

For WI-5: the verification must describe the bytes that were WRITTEN. Verifying the
pre-normalization text describes bytes nobody will ever read, because `normalize.process()` is what
produces the shelf artifact.
"""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from klode import lib                                                     # noqa: E402
from klode.lib import agreement, coverage, integrity                      # noqa: E402
from klode.lib.ingest import VerificationError, ingest                    # noqa: E402


def _kb(root: Path) -> Path:
    (root / "library" / "books").mkdir(parents=True, exist_ok=True)
    (root / "library" / "cards").mkdir(exist_ok=True)
    (root / "library.toml").write_text(
        '[library]\ndir = "library"\ncards = "cards"\nshelves = ["books"]\n'
        '[bibliography]\nenabled = false\n', encoding="utf-8")
    return root / "library.toml"


class States(unittest.TestCase):
    """Four states, because 'did not run' and 'ran and failed' are different facts."""

    def _agree(self, control, candidate):
        return agreement.compare(control, candidate, window=50)

    def test_clean_extraction_verifies(self):
        a = " ".join(f"tok{i}" for i in range(400))
        v = integrity.decide(self._agree(a, a), coverage.assess(2, "x\fy\f", (1, 2)))
        self.assertEqual(v.state, integrity.VERIFIED)
        self.assertTrue(v.ok)
        self.assertFalse(v.blocks_write)

    def test_dropped_material_fails(self):
        a = " ".join(f"tok{i}" for i in range(400))
        b = " ".join(f"tok{i}" for i in range(200))
        v = integrity.decide(self._agree(a, b), None)
        self.assertEqual(v.state, integrity.FAILED)
        self.assertTrue(v.blocks_write)
        self.assertTrue(any("containment" in r for r in v.reasons))

    def test_duplicated_material_fails(self):
        a = " ".join(f"tok{i}" for i in range(400))
        v = integrity.decide(self._agree(a, a + " " + a), None)
        self.assertEqual(v.state, integrity.FAILED)
        self.assertTrue(any("inflation" in r for r in v.reasons))

    def test_inverted_reading_order_fails(self):
        toks = [f"tok{i}" for i in range(400)]
        scrambled = []
        for i in range(0, 400, 50):
            scrambled.extend(reversed(toks[i:i + 50]))
        v = integrity.decide(self._agree(" ".join(toks), " ".join(scrambled)), None)
        self.assertEqual(v.state, integrity.FAILED)
        self.assertTrue(any("reading order" in r for r in v.reasons))

    def test_a_missing_candidate_page_fails(self):
        a = " ".join(f"tok{i}" for i in range(400))
        cov = coverage.assess(3, "a\fb\fc\f", candidate_pages=(1, 3))
        v = integrity.decide(self._agree(a, a), cov)
        self.assertEqual(v.state, integrity.FAILED)
        self.assertTrue(any("omits declared page" in r for r in v.reasons))

    def test_nothing_measurable_abstains_and_abstention_is_not_ok(self):
        v = integrity.decide(None, None)
        self.assertEqual(v.state, integrity.ABSTAINED)
        self.assertFalse(v.ok)               # NOT ok — 'unknown', not 'fine'
        self.assertFalse(v.blocks_write)     # but it must not block every unmeasurable PDF either

    def test_an_override_keeps_its_evidence(self):
        a = " ".join(f"tok{i}" for i in range(400))
        b = " ".join(f"tok{i}" for i in range(200))
        failed = integrity.decide(self._agree(a, b), None)
        accepted = integrity.Integrity(integrity.UNVERIFIED, failed.reasons,
                                       failed.metrics, failed.thresholds)
        self.assertEqual(accepted.state, integrity.UNVERIFIED)
        self.assertTrue(accepted.reasons)                    # why it failed survives the override
        self.assertIn("containment", accepted.metrics)

    def test_every_decision_carries_the_thresholds_that_judged_it(self):
        # thresholds are provisional; a record without them cannot be recalibrated later
        v = integrity.decide(self._agree("a b c", "a b c"), None)
        self.assertIn("min_containment", v.thresholds)
        self.assertIn("min_median_order", v.thresholds)


class TransactionalSemantics(unittest.TestCase):
    """Either success + artifact, or failure + no mutation. Never both."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-ingest-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cfg = lib.Config.load(_kb(self.tmp))
        self.src = self.tmp / "source.txt"
        self.src.write_text(" ".join(f"word{i}" for i in range(500)), encoding="utf-8")

    def _shelf(self):
        return sorted(p.name for p in (self.tmp / "library" / "books").glob("*.txt"))

    def test_success_promotes_the_artifact(self):
        r = ingest(self.cfg, self.src, "books", card_id="ok")
        self.assertEqual(self._shelf(), ["ok.txt"])
        self.assertIsNotNone(r.verification)

    def test_a_failed_verification_leaves_no_shelf_source(self):
        # forced failure via a stub verifier: the point is the WRITE semantics, not the metric
        import klode.lib.ingest as ing
        original = ing.verify_extraction
        ing.verify_extraction = lambda *a, **k: integrity.Integrity(
            integrity.FAILED, ("stubbed failure",), {}, {})
        self.addCleanup(setattr, ing, "verify_extraction", original)
        with self.assertRaises(VerificationError):
            ingest(self.cfg, self.src, "books", card_id="bad")
        self.assertEqual(self._shelf(), [])                  # nothing promoted
        prov = self.tmp / "library" / "PROVENANCE.jsonl"
        self.assertFalse(prov.exists(), "a refused ingest must not record provenance either")

    def test_the_retry_after_a_failure_is_not_blocked_by_a_leftover_file(self):
        # v1's incoherence: exit non-zero AND write, so the retry hits an existing file and needs
        # --force. Here the first attempt left nothing, so the retry is a clean first attempt.
        import klode.lib.ingest as ing
        original = ing.verify_extraction
        ing.verify_extraction = lambda *a, **k: integrity.Integrity(
            integrity.FAILED, ("stubbed failure",), {}, {})
        with self.assertRaises(VerificationError):
            ingest(self.cfg, self.src, "books", card_id="retry")
        ing.verify_extraction = original                     # the condition clears
        r = ingest(self.cfg, self.src, "books", card_id="retry")   # no --force needed
        self.assertEqual(self._shelf(), ["retry.txt"])
        self.assertIsNotNone(r)

    def test_accept_unverified_writes_and_records_the_failure(self):
        import klode.lib.ingest as ing
        original = ing.verify_extraction
        ing.verify_extraction = lambda *a, **k: integrity.Integrity(
            integrity.FAILED, ("stubbed failure",), {"containment": 0.1}, {})
        self.addCleanup(setattr, ing, "verify_extraction", original)
        r = ingest(self.cfg, self.src, "books", card_id="accepted", accept_unverified=True)
        self.assertEqual(self._shelf(), ["accepted.txt"])
        self.assertEqual(r.verification.state, integrity.UNVERIFIED)
        self.assertTrue(r.verification.reasons)              # the finding is not erased
        row = json.loads((self.tmp / "library" / "PROVENANCE.jsonl").read_text().splitlines()[-1])
        self.assertEqual(row["verification"]["status"], "unverified")
        self.assertEqual(row["verification"]["metrics"]["containment"], 0.1)


class ProvenanceBoundToBytes(unittest.TestCase):
    """WI-5: the record must describe what was written."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-prov-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cfg = lib.Config.load(_kb(self.tmp))
        self.src = self.tmp / "s.txt"
        self.src.write_text(" ".join(f"word{i}" for i in range(500)), encoding="utf-8")

    def _rows(self):
        p = self.tmp / "library" / "PROVENANCE.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

    def test_output_sha256_matches_the_persisted_file(self):
        ingest(self.cfg, self.src, "books", card_id="b")
        written = (self.tmp / "library" / "books" / "b.txt").read_bytes()
        self.assertEqual(self._rows()[-1]["output_sha256"],
                         hashlib.sha256(written).hexdigest())

    def test_changing_one_byte_orphans_the_verification_record(self):
        ingest(self.cfg, self.src, "books", card_id="c")
        row = self._rows()[-1]
        shelf = self.tmp / "library" / "books" / "c.txt"
        shelf.write_text(shelf.read_text(encoding="utf-8") + "x", encoding="utf-8")
        now = hashlib.sha256(shelf.read_bytes()).hexdigest()
        self.assertNotEqual(row["output_sha256"], now,
                            "the record must no longer describe the file on disk")

    def test_a_forced_reingest_does_not_inherit_the_previous_verification(self):
        ingest(self.cfg, self.src, "books", card_id="d")
        first = self._rows()[-1]
        self.src.write_text(" ".join(f"other{i}" for i in range(500)), encoding="utf-8")
        ingest(self.cfg, self.src, "books", card_id="d", force=True)
        second = self._rows()[-1]
        self.assertNotEqual(first["output_sha256"], second["output_sha256"])
        self.assertEqual(len(self._rows()), 2)               # a NEW row, not an amended one

    def test_the_record_carries_schema_thresholds_and_both_tiers(self):
        ingest(self.cfg, self.src, "books", card_id="e")
        v = self._rows()[-1]["verification"]
        self.assertEqual(v["schema"], integrity.SCHEMA)
        self.assertIn("thresholds", v)
        self.assertIn("control_tier", v)
        self.assertIn("candidate_tier", v)
        self.assertIn(v["status"], ("verified", "unverified", "abstained", "failed"))

    def test_a_non_pdf_abstains_rather_than_claiming_verification(self):
        r = ingest(self.cfg, self.src, "books", card_id="f")
        self.assertEqual(r.verification.state, integrity.ABSTAINED)
        self.assertFalse(r.verification.ok)


if __name__ == "__main__":
    unittest.main()


class VerifyExtractionOnRealText(unittest.TestCase):
    """Gap found in the Step-4 audit: `verify_extraction` — the function that runs the control,
    normalizes BOTH sides, and compares — was only ever reached through stubs. Everything below
    drives it with a real PDF and real poppler output.

    Without an OCR backend installed the winning tier is the control itself, so the pipeline
    always abstains; these construct the candidate directly to exercise the path that a
    docling/marker install would take.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-verifyreal-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cfg = lib.Config.load(_kb(self.tmp))
        self.pdf = REPO / "tests" / "fixtures" / "pdfs" / "three-pages.pdf"

    def _run(self, candidate_text, handler="docling", pages=None):
        from klode.lib.formats._base import Extraction
        from klode.lib.ingest import verify_extraction
        from klode.lib.normalize import load_dict
        ext = Extraction(text=candidate_text, handler=handler, format="pdf", pages=pages)
        return verify_extraction(self.cfg, self.pdf, ext, candidate_text, load_dict(self.cfg.dict_path))

    @unittest.skipUnless(shutil.which("pdftotext"), "poppler not installed")
    def test_a_faithful_candidate_verifies_against_the_real_control(self):
        import subprocess as sp
        control = sp.run(["pdftotext", "-layout", str(self.pdf), "-"],
                         capture_output=True, text=True, timeout=60).stdout
        v = self._run(control)
        self.assertEqual(v.state, integrity.VERIFIED, v.reasons)
        self.assertTrue(v.ok)
        self.assertIn("containment", v.metrics)

    @unittest.skipUnless(shutil.which("pdftotext"), "poppler not installed")
    def test_a_candidate_missing_half_the_document_fails(self):
        import subprocess as sp
        control = sp.run(["pdftotext", "-layout", str(self.pdf), "-"],
                         capture_output=True, text=True, timeout=60).stdout
        half = " ".join(control.split()[:len(control.split()) // 2])
        v = self._run(half)
        self.assertEqual(v.state, integrity.FAILED)
        self.assertTrue(any("containment" in r or "inflation" in r for r in v.reasons))

    @unittest.skipUnless(shutil.which("pdftotext"), "poppler not installed")
    def test_a_candidate_omitting_a_declared_page_fails_on_coverage(self):
        import subprocess as sp
        control = sp.run(["pdftotext", "-layout", str(self.pdf), "-"],
                         capture_output=True, text=True, timeout=60).stdout
        v = self._run(control, pages=(1, 2))            # claims 2 of 3 declared pages
        self.assertEqual(v.state, integrity.FAILED)
        self.assertTrue(any("omits declared page" in r for r in v.reasons))
        self.assertEqual(v.metrics["candidate_missing_pages"], [3])

    def test_the_control_tier_being_the_candidate_abstains(self):
        # the real-world case here: with no OCR backend installed, pdftotext wins and there is
        # nothing independent to compare against. Abstain — never claim verification.
        v = self._run("whatever", handler="pdftotext")
        self.assertEqual(v.state, integrity.ABSTAINED)
        self.assertFalse(v.ok)

    def test_a_non_pdf_abstains(self):
        from klode.lib.formats._base import Extraction
        from klode.lib.ingest import verify_extraction
        from klode.lib.normalize import load_dict
        ext = Extraction(text="x", handler="txt", format="txt")
        v = verify_extraction(self.cfg, self.pdf, ext, "x", load_dict(self.cfg.dict_path))
        self.assertEqual(v.state, integrity.ABSTAINED)
