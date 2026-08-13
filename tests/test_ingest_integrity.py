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
import os
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

    def test_an_unwritable_provenance_log_leaves_no_shelf_artifact(self):
        # the first direction: promote-then-record left a shelf artifact behind when the log could
        # not be appended — failure PLUS a mutation.
        prov = self.tmp / "library" / "PROVENANCE.jsonl"
        prov.parent.mkdir(parents=True, exist_ok=True)
        prov.write_text("", encoding="utf-8")
        os.chmod(prov, 0o400)
        self.addCleanup(os.chmod, prov, 0o600)
        if os.access(prov, os.W_OK):                      # root ignores the mode
            self.skipTest("running as a user who can write a read-only file")
        with self.assertRaises(OSError):
            ingest(self.cfg, self.src, "books", card_id="noprov")
        self.assertEqual(self._shelf(), [])

    def test_a_failed_promotion_leaves_no_provenance_row(self):
        # the OTHER direction, introduced by fixing the first: recording before promoting left a
        # durable row describing an artifact that never landed.
        import klode.lib.ingest as ing
        real_replace = ing.os.replace

        def boom(src, dst):
            raise OSError("simulated promotion failure")
        ing.os.replace = boom
        self.addCleanup(setattr, ing.os, "replace", real_replace)
        prov = self.tmp / "library" / "PROVENANCE.jsonl"
        prov.parent.mkdir(parents=True, exist_ok=True)
        prov.write_text('{"id": "earlier"}\n', encoding="utf-8")
        with self.assertRaises(OSError):
            ingest(self.cfg, self.src, "books", card_id="norow")
        self.assertEqual(self._shelf(), [])
        self.assertEqual(prov.read_text(encoding="utf-8"), '{"id": "earlier"}\n',
                         "the rolled-back row must go, and the earlier one must stay")
        self.assertEqual(list((self.tmp / "library" / "books").glob(".*")), [],
                         "the temp file must not be orphaned either")

    def test_a_concurrent_row_is_never_destroyed_by_a_rollback(self):
        # the rollback truncates a log; it must refuse when anything else has appended since, or
        # tidying up this ingest deletes another one's record
        from klode.lib.ingest import _rollback_provenance
        prov = self.tmp / "rollback.jsonl"
        prov.write_text("a\n", encoding="utf-8")
        before = 2
        prov.write_text("a\nmine\nsomeone-else\n", encoding="utf-8")
        _rollback_provenance(prov, before, len("mine\n"))
        self.assertEqual(prov.read_text(encoding="utf-8"), "a\nmine\nsomeone-else\n")
        # ...and it does truncate when the log is exactly as this call left it
        prov.write_text("a\nmine\n", encoding="utf-8")
        _rollback_provenance(prov, before, len("mine\n"))
        self.assertEqual(prov.read_text(encoding="utf-8"), "a\n")

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


class FailOpensClosedInAudit(unittest.TestCase):
    """States that returned a confident `verified` on no evidence. Each was a real fail-open."""

    def _agree(self, control, candidate, **kw):
        return agreement.compare(control, candidate, window=50, **kw)

    def test_all_windows_abstaining_is_not_verified(self):
        # every token repeated -> no unique anchors -> order unmeasurable. Containment and
        # inflation still pass, and the verdict used to be `verified`: a confident pass on text
        # whose reading order was never measured at all.
        a = " ".join(["same"] * 500)
        r = self._agree(a, a)
        self.assertEqual(r.measured, ())
        v = integrity.decide(r, None)
        self.assertEqual(v.state, integrity.ABSTAINED)
        self.assertFalse(v.ok)

    def test_one_fully_scrambled_page_among_good_ones_is_not_verified(self):
        # median stays ~1.0 while one window is inverted; a median-only gate buried it, which
        # directly contradicted the docstring promising one bad page is a real finding
        toks = [f"tok{i}" for i in range(500)]
        b = list(toks)
        b[100:150] = list(reversed(b[100:150]))
        r = self._agree(" ".join(toks), " ".join(b))
        self.assertGreater(r.quantile(0.50), 0.99)          # the median is still fine...
        self.assertLess(r.worst_window.order, -0.9)         # ...and the bad page is still there
        self.assertEqual(integrity.decide(r, None).state, integrity.FAILED)

    def test_coverage_that_cannot_speak_for_the_candidate_is_not_verified(self):
        cov = coverage.assess(3, "a\fb\fc\f", candidate_pages=None)
        self.assertFalse(cov.candidate_known)
        self.assertEqual(integrity.decide(None, cov).state, integrity.ABSTAINED)

    def test_an_invalid_state_string_cannot_be_constructed(self):
        # `Integrity("faield").blocks_write` was False — a typo in the field that gates the write
        with self.assertRaises(ValueError):
            integrity.Integrity("faield")

    def test_a_nan_threshold_is_refused(self):
        # NaN makes every comparison False, turning a failure into `verified`
        with self.assertRaises(ValueError):
            integrity.Thresholds(min_containment=float("nan"))
        with self.assertRaises(ValueError):
            integrity.Thresholds(min_inflation=2.0, max_inflation=1.0)

    def test_non_latin_text_is_compared_rather_than_discarded(self):
        # an ASCII-only tokenizer reduced two unrelated documents to the same residue and
        # verified them
        a = "共通 a1 a2 a3 a4 a5 a6 a7 a8 完全に異なる内容がここにあります"
        b = "共通 a1 a2 a3 a4 a5 a6 a7 a8 совершенно другой текст здесь"
        self.assertLess(self._agree(a, b).containment, 0.95)

    # ---- round 3: fail-opens an independent verification pass found still open ----

    def test_a_non_finite_measurement_cannot_reach_a_verdict(self):
        # NaN makes every threshold comparison False, so the gate saw "nothing exceeded a limit"
        # and returned `verified` with NaN in the metrics — and in the provenance row.
        with self.assertRaises(ValueError):
            agreement.Agreement(containment=float("nan"), inflation=1.0, control_tokens=10,
                                candidate_tokens=10, windows=())
        with self.assertRaises(ValueError):
            agreement.Agreement(containment=1.0, inflation=1.0, control_tokens=10,
                                candidate_tokens=10,
                                windows=(agreement.Window(0, 0, 9, float("nan")),))

    def test_order_measured_on_only_half_the_windows_is_not_verified(self):
        # one measurable page + one page too short to measure is a verdict about one page.
        w = (agreement.Window(0, 0, 9, 1.0), agreement.Window(1, 10, 7, None))
        a = agreement.Agreement(containment=1.0, inflation=1.0, control_tokens=20,
                                candidate_tokens=20, windows=w)
        self.assertEqual(a.measured_share, 0.5)
        v = integrity.decide(a, None)
        self.assertEqual(v.state, integrity.ABSTAINED)
        self.assertTrue(any("not a majority" in r for r in v.reasons))
        self.assertEqual(v.metrics["order_measured_share"], 0.5)

    def test_a_strict_majority_of_measured_windows_still_verifies(self):
        w = (agreement.Window(0, 0, 9, 1.0), agreement.Window(1, 10, 9, 1.0),
             agreement.Window(2, 20, 7, None))
        a = agreement.Agreement(containment=1.0, inflation=1.0, control_tokens=30,
                                candidate_tokens=30, windows=w)
        self.assertEqual(integrity.decide(a, None).state, integrity.VERIFIED)

    def test_coverage_with_an_unknown_declared_page_count_is_not_verified(self):
        # `declared=0` means pdfinfo could not say. `candidate_missing` is then empty for the
        # trivial reason that there is nothing to be missing from — which read as a clean pass.
        cov = coverage.assess(0, "", candidate_pages=(1,))
        self.assertTrue(cov.candidate_known)
        v = integrity.decide(None, cov)
        self.assertEqual(v.state, integrity.ABSTAINED)
        self.assertFalse(v.ok)

    def test_the_reported_order_median_is_a_median_not_a_nearest_rank_pick(self):
        # `quantile(0.5)` on [-1.0, 1.0] returns an OBSERVATION (-1.0 or 1.0 depending on
        # rounding), which is not the midpoint the field name promises.
        w = (agreement.Window(0, 0, 9, -1.0), agreement.Window(1, 10, 9, 1.0))
        a = agreement.Agreement(containment=1.0, inflation=1.0, control_tokens=20,
                                candidate_tokens=20, windows=w)
        self.assertEqual(a.median, 0.0)
        self.assertIn(a.quantile(0.50), (-1.0, 1.0))        # a nearest-rank pick, by definition
        v = integrity.decide(a, None)
        self.assertEqual(v.metrics["order_median"], 0.0,    # the RECORDED number, not just the API
                         "the provenance row must carry a median, not one of the two observations")
        # ...and the inverted page is still caught, by the worst-window gate rather than the median
        self.assertEqual(v.state, integrity.FAILED)


class ControlNormalizationMatchesTheCandidatesPipeline(unittest.TestCase):
    """Both sides must go through the SAME normalization, or the comparison manufactures failures.

    Normalizing the control page-at-a-time is a different pipeline: `strip_page_furniture` only
    recognizes a running head that repeats >=5 times across the text it is given, so a head the
    whole-document candidate lost survives on every control page.
    """

    def _pages(self, n=6):
        return [f"CHAPTER ONE\n\npage {i} body text alpha beta gamma delta {i}\n"
                for i in range(1, n + 1)]

    def test_running_heads_are_stripped_from_the_control_too(self):
        from klode.lib.ingest import _normalize_control
        raw = "\f".join(self._pages())
        text, pages = _normalize_control(raw, set())
        self.assertNotIn("CHAPTER ONE", text)
        self.assertIsNotNone(pages)
        self.assertEqual(len(pages), 6)

    def test_page_boundaries_survive_whole_document_normalization(self):
        from klode.lib.ingest import _normalize_control
        raw = "\f".join(self._pages())
        _text, pages = _normalize_control(raw, set())
        for i, page in enumerate(pages, start=1):
            self.assertIn(f"delta {i}", page, f"page {i} lost its own content")

    def test_the_control_and_candidate_agree_when_the_extraction_is_faithful(self):
        # the manufactured failure: page-wise control kept 6 running heads the candidate dropped,
        # so containment fell for an extraction that is byte-identical after normalization
        from klode.lib.ingest import _normalize_control
        from klode.lib.normalize import process
        raw = "\f".join(self._pages())
        control, pages = _normalize_control(raw, set())
        candidate = process(raw, set())[0]                # the pipeline every candidate goes through
        a = agreement.compare(control, candidate, control_pages=pages)
        self.assertGreaterEqual(a.containment, 0.99)
        self.assertAlmostEqual(a.inflation, 1.0, places=2)

    def test_unrecoverable_boundaries_degrade_to_windows_rather_than_to_a_wrong_number(self):
        from klode.lib.ingest import _PAGE_MARK, _normalize_control
        raw = f"{_PAGE_MARK} a b c\fd e f"                 # the marker already occurs in the source
        text, pages = _normalize_control(raw, set())
        self.assertIsNone(pages)                           # no page claim...
        self.assertTrue(text.strip())                      # ...but the text is still normalized once


if __name__ == "__main__":
    unittest.main()
