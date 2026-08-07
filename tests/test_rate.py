"""eval/rate.py — the rubric's acceptance instrument must itself fail closed.

An agreement harness that reports a clean verdict when it cannot support one is worse than no
harness: it certifies a rubric nobody actually agreed on. Every test here is a way the old version
printed success without evidence — undefined kappa, vanished rows, one rater compared with itself.
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

from klode import lib                                                      # noqa: E402
from klode.gate import load_spec                                           # noqa: E402

_spec_mod = importlib.util.spec_from_file_location("klode_rate", REPO / "eval" / "rate.py")
rate = importlib.util.module_from_spec(_spec_mod)
_spec_mod.loader.exec_module(rate)

FIX = REPO / "tests" / "fixtures" / "kb-fixture" / "library.toml"


def _sheet(rows, *, rater="alice", rubric="R1", drafts="D1", dim="pacing"):
    out = [json.dumps({"_sheet": {"schema": rate.SHEET_SCHEMA, "rater": rater, "dimension": dim,
                                  "rubric_digest": rubric, "draft_digest": drafts,
                                  "rows": len(rows)}})]
    for draft, crit, score, top in rows:
        out.append(json.dumps({"rater": rater, "draft": draft, "criterion": crit,
                               "score": score, "max_score": top}))
    return "\n".join(out) + "\n"


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-rate-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _run(self, rows_a, rows_b, *, bar=0.6, **kw):
        a, b = self.tmp / "a.jsonl", self.tmp / "b.jsonl"
        a.write_text(_sheet(rows_a, rater="alice", **kw))
        b.write_text(_sheet(rows_b, rater="bob", **kw))
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = rate.main(["score", str(a), str(b), "--bar", str(bar)])
        return code, buf.getvalue()

    # --- the kappa itself ---

    def test_qwk_matches_the_standard_definition(self):
        # Hand-derived, NOT copied from the implementation (which would only test that it repeats
        # itself). pairs = [(0,0),(1,1),(2,2),(0,1),(2,1)], k=3, n=5:
        #   O = [[1,1,0],[0,1,0],[0,1,1]]; row marginals [2,1,2]; col marginals [1,3,1]
        #   w[i][j] = (i-j)^2 / (k-1)^2 = (i-j)^2 / 4
        #   weighted observed = 0.25*O[0][1] + 0.25*O[2][1]                       = 0.5
        #   weighted expected = 0.25*1.2 + 1.0*0.4 + 0.25*0.2
        #                     + 0.25*0.2 + 1.0*0.4 + 0.25*1.2                     = 1.5
        #   kappa = 1 - 0.5/1.5 = 2/3
        pairs = [(0, 0), (1, 1), (2, 2), (0, 1), (2, 1)]
        self.assertAlmostEqual(rate._qwk(pairs, 3), 2 / 3, places=10)
        self.assertEqual(rate._qwk([(0, 0), (1, 1), (2, 2)], 3), 1.0)          # perfect
        self.assertLess(rate._qwk([(0, 2), (2, 0), (0, 2), (2, 0)], 3), 0)     # systematic inversion

    def test_qwk_is_none_when_undefined(self):
        self.assertIsNone(rate._qwk([(1, 1), (1, 1)], 6))     # both constant: nothing to agree about
        self.assertIsNone(rate._qwk([(1, 2)], 6))             # a single observation
        self.assertIsNone(rate._qwk([(0, 0), (1, 1)], 1))     # degenerate scale

    # --- the failures that used to print a clean verdict ---

    def test_undefined_kappa_is_a_failure_not_a_pass(self):
        rows = [("d1", "c", 1, 5), ("d2", "c", 1, 5)]
        code, out = self._run(rows, rows)
        self.assertEqual(code, 1)
        self.assertIn("UNDEFINED", out)
        self.assertIn("not ready", out)
        self.assertNotIn("applied consistently", out)

    def test_rows_unfilled_in_both_sheets_cannot_be_skipped(self):
        rows = [("d1", "c", 3, 5), ("d2", "c", 1, 5), ("d3", "c", None, 5)]
        with self.assertRaises(SystemExit) as e:
            self._run(rows, rows)
        self.assertIn("unscored", str(e.exception))

    def test_a_row_missing_from_one_sheet_is_refused(self):
        with self.assertRaises(SystemExit) as e:
            self._run([("d1", "c", 3, 5), ("d2", "c", 1, 5)], [("d1", "c", 3, 5)])
        self.assertIn("same rows", str(e.exception))

    def test_the_same_rater_twice_is_refused(self):
        rows = [("d1", "c", 3, 5), ("d2", "c", 1, 5)]
        a = self.tmp / "a.jsonl"
        a.write_text(_sheet(rows, rater="alice"))
        with self.assertRaises(SystemExit) as e:
            rate.main(["score", str(a), str(a), "--bar", "0.6"])
        self.assertIn("two raters", str(e.exception))

    def test_mismatched_rubric_or_draft_set_is_refused(self):
        rows = [("d1", "c", 3, 5), ("d2", "c", 1, 5)]
        for kw, word in (({"rubric": "R2"}, "rubric"), ({"drafts": "D2"}, "draft set")):
            with self.subTest(**kw):
                a, b = self.tmp / "a.jsonl", self.tmp / "b.jsonl"
                a.write_text(_sheet(rows, rater="alice"))
                b.write_text(_sheet(rows, rater="bob", **kw))
                with self.assertRaises(SystemExit) as e:
                    rate.main(["score", str(a), str(b)])
                self.assertIn(word, str(e.exception))

    def test_scale_disagreement_between_sheets_is_refused(self):
        with self.assertRaises(SystemExit) as e:
            self._run([("d1", "c", 1, 5), ("d2", "c", 0, 5)],
                      [("d1", "c", 1, 3), ("d2", "c", 0, 3)])
        self.assertIn("different scales", str(e.exception))

    # --- untrusted row content ---

    def test_malformed_rows_are_refused(self):
        cases = [
            ([("d1", "c", 1.5, 5)], "must be an integer"),      # float score
            ([("d1", "c", True, 5)], "must be an integer"),     # bool score
            ([("d1", "c", -1, 5)], "outside this criterion"),   # negative would index from the end
            ([("d1", "c", 9, 5)], "outside this criterion"),    # above the scale -> IndexError before
            ([("d1", "c", 1, 1)], "max_score"),                 # degenerate scale
            ([("d1", "c", 1, 10**9)], "max_score"),             # k x k allocation
        ]
        for rows, word in cases:
            with self.subTest(rows=rows):
                p = self.tmp / "x.jsonl"
                p.write_text(_sheet(rows + [("d2", "c", 1, 5)]))
                with self.assertRaises(SystemExit) as e:
                    rate.main(["score", str(p), str(p)])
                self.assertIn(word, str(e.exception))

    def test_duplicate_rows_are_refused(self):
        p = self.tmp / "x.jsonl"
        p.write_text(_sheet([("d1", "c", 1, 5), ("d1", "c", 4, 5)]))
        with self.assertRaises(SystemExit) as e:
            rate.main(["score", str(p), str(p)])
        self.assertIn("duplicate row", str(e.exception))

    def test_a_sheet_without_a_header_is_refused(self):
        p = self.tmp / "x.jsonl"
        p.write_text(json.dumps({"draft": "d", "criterion": "c", "score": 1, "max_score": 5}) + "\n")
        with self.assertRaises(SystemExit) as e:
            rate.main(["score", str(p), str(p)])
        self.assertIn("_sheet", str(e.exception))

    def test_a_non_finite_bar_is_refused(self):
        # `--bar nan` made every row under-specified AND the run succeed, since both comparisons fail
        rows = [("d1", "c", 3, 5), ("d2", "c", 1, 5)]
        for bad in ("nan", "1.5", "-0.1"):
            with self.subTest(bar=bad), self.assertRaises(SystemExit) as e:
                self._run(rows, rows, bar=bad)
            self.assertIn("--bar", str(e.exception))

    # --- the happy path still works, and still discriminates ---

    def test_agreement_passes_and_disagreement_fails_per_criterion(self):
        agree = [(f"d{i}", "good", s, 5) for i, s in enumerate([0, 1, 2, 3, 4, 5])]
        rows_a = agree + [(f"d{i}", "vague", s, 5) for i, s in enumerate([0, 1, 2, 3, 4, 5])]
        rows_b = agree + [(f"d{i}", "vague", s, 5) for i, s in enumerate([5, 4, 3, 2, 1, 0])]
        code, out = self._run(rows_a, rows_b)
        self.assertEqual(code, 1)
        self.assertRegex(out, r"good\s+6\s+100%.*\bok\b")
        self.assertIn("UNDER-SPECIFIED", out)
        self.assertIn("vague", out)

    def test_all_criteria_agreeing_passes(self):
        rows = [(f"d{i}", "c", s, 5) for i, s in enumerate([0, 1, 2, 3, 4, 5])]
        code, out = self._run(rows, rows)
        self.assertEqual(code, 0)
        self.assertIn("applied consistently", out)

    def test_overall_kappa_is_withheld_for_mixed_scales(self):
        rows = [("d1", "a", 0, 5), ("d2", "a", 5, 5), ("d1", "b", 0, 3), ("d2", "b", 3, 3)]
        code, out = self._run(rows, rows)
        self.assertEqual(code, 0)
        self.assertIn("scales differ", out)          # pooling 3-of-3 with 3-of-10 would be meaningless

    # --- sheet generation ---

    def test_sheet_pins_rubric_and_drafts_and_orders_per_rater(self):
        drafts = self.tmp / "drafts"
        drafts.mkdir()
        for n in "abcdefgh":
            (drafts / f"{n}.md").write_text(f"draft {n}\n")
        sheets = {}
        for rater in ("alice", "bob"):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rate.main(["sheet", "-c", str(FIX), "pacing", "--drafts", str(drafts),
                           "--rater", rater])
            sheets[rater] = [json.loads(l) for l in buf.getvalue().splitlines()]
        ha, hb = sheets["alice"][0]["_sheet"], sheets["bob"][0]["_sheet"]
        self.assertEqual(ha["rubric_digest"], hb["rubric_digest"])   # same rubric
        self.assertEqual(ha["draft_digest"], hb["draft_digest"])     # same drafts
        self.assertNotEqual(ha["rater"], hb["rater"])
        order_a = [r["draft"] for r in sheets["alice"][1:]]
        order_b = [r["draft"] for r in sheets["bob"][1:]]
        self.assertEqual(sorted(order_a), sorted(order_b))
        self.assertNotEqual(order_a, order_b)                        # decorrelated with 8 drafts
        self.assertTrue(all(r["score"] is None for r in sheets["alice"][1:]))

    def test_same_stem_different_extension_are_two_drafts(self):
        drafts = self.tmp / "d2"
        drafts.mkdir()
        (drafts / "x.md").write_text("one\n")
        (drafts / "x.txt").write_text("two\n")
        ids = {d for d, _ in rate._drafts(drafts)}
        self.assertEqual(ids, {"x.md", "x.txt"})     # stem-only identity silently dropped one

    def test_inline_embeds_each_draft_once_not_once_per_criterion(self):
        drafts = self.tmp / "d3"
        drafts.mkdir()
        (drafts / "only.md").write_text("BODY\n")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rate.main(["sheet", "-c", str(FIX), "pacing", "--drafts", str(drafts),
                       "--rater", "alice", "--inline"])
        rows = [json.loads(l) for l in buf.getvalue().splitlines()]
        self.assertEqual(sum(1 for r in rows if "_draft" in r), 1)
        self.assertGreater(sum(1 for r in rows if "criterion" in r), 1)




class Round2(unittest.TestCase):
    """Defects introduced by, or missed in, the round-1 harness fixes."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-rate2-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _files(self, rows_a, rows_b, **kw):
        a, b = self.tmp / "a.jsonl", self.tmp / "b.jsonl"
        a.write_text(_sheet(rows_a, rater="alice", **kw))
        b.write_text(_sheet(rows_b, rater="bob", **kw))
        return a, b

    def test_homogeneous_scales_are_not_reported_as_differing(self):
        # `scales.pop()` emptied the set that the very next line tested, so EVERY run said
        # "scales differ" — including one where every criterion shared a scale
        rows = [(f"d{i}", "c", s, 5) for i, s in enumerate([0, 2, 4, 5])]
        a, b = self._files(rows, rows)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = rate.main(["score", str(a), str(b)])
        out = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertNotIn("scales differ", out)
        self.assertRegex(out, r"OVERALL.*1\.000")

    def test_one_criterion_with_two_scales_is_refused(self):
        # k was derived from the first row, so a later larger score indexed past the matrix
        rows = [("d1", "c", 0, 2), ("d2", "c", 5, 5)]
        a, b = self._files(rows, rows)
        with self.assertRaises(SystemExit) as e:
            rate.main(["score", str(a), str(b)])
        self.assertIn("more than one scale", str(e.exception))

    def test_headers_missing_their_identity_do_not_compare_equal(self):
        rows = [("d1", "c", 3, 5), ("d2", "c", 1, 5)]
        a, b = self.tmp / "a.jsonl", self.tmp / "b.jsonl"
        for p, rater in ((a, "alice"), (b, "bob")):
            head = json.dumps({"_sheet": {"schema": rate.SHEET_SCHEMA, "rater": rater,
                                          "rows": len(rows)}})       # no digests at all
            body = "\n".join(json.dumps({"rater": rater, "draft": d, "criterion": c,
                                         "score": s, "max_score": t}) for d, c, s, t in rows)
            p.write_text(head + "\n" + body + "\n")
        with self.assertRaises(SystemExit) as e:                     # None == None used to pass
            rate.main(["score", str(a), str(b)])
        self.assertIn("missing its", str(e.exception))

    def test_deleting_the_same_row_from_both_sheets_is_caught(self):
        # equal inventories looked "complete"; only the header's declared count reveals the deletion
        rows = [("d1", "c", 3, 5), ("d2", "c", 1, 5), ("d3", "c", 2, 5)]
        a, b = self.tmp / "a.jsonl", self.tmp / "b.jsonl"
        for p, rater in ((a, "alice"), (b, "bob")):
            text = _sheet(rows, rater=rater)
            p.write_text("\n".join(text.splitlines()[:-1]) + "\n")   # header still says 3
        with self.assertRaises(SystemExit) as e:
            rate.main(["score", str(a), str(b)])
        self.assertIn("header declares", str(e.exception))

    def test_a_reworded_rubric_is_not_interchangeable(self):
        # Goes through the REAL rubric_identity(), not a hand-rebuilt copy of its inputs: the old
        # version of this test would still pass if cmd_sheet reverted to hashing ids and maxima.
        cfg = lib.Config.load(FIX)
        spec = load_spec(cfg, "pacing")
        before = rate.rubric_identity(spec)

        class Reworded:                      # same ids, same scales, different words
            dimension, panel, criteria = spec.dimension, spec.panel, [
                type("C", (), {
                    "id": c.id, "max_score": c.max_score, "statement": c.statement,
                    "guidance": c.guidance, "fields": c.fields, "evidence": c.evidence,
                    "cards": c.cards,
                    "levels": [type("L", (), {"score": l.score, "descriptor": type("F", (), {
                        "value": l.descriptor.value + " (reworded)", "kind": l.descriptor.kind,
                        "warrant": l.descriptor.warrant, "evidence": (), "cards": ()})()})()
                        for l in c.levels],
                })() for c in spec.criteria]
        self.assertNotEqual(before, rate.rubric_identity(Reworded()))

    def test_the_digest_does_not_collide_on_separator_shuffling(self):
        # `a:b` + `c` used to serialize identically to `a` + `b:c`; None collided with "None"
        self.assertNotEqual(rate._canonical_digest({"s": "a:b", "g": "c"}),
                            rate._canonical_digest({"s": "a", "g": "b:c"}))
        self.assertNotEqual(rate._canonical_digest({"g": None}),
                            rate._canonical_digest({"g": "None"}))

    def test_a_missing_or_mistyped_row_count_is_refused(self):
        # the count check only ran when `rows` was already a non-bool int, so omitting it, or
        # sending "3"/null/true, skipped the deletion guard entirely
        rows = [("d1", "c", 3, 5), ("d2", "c", 1, 5)]
        for bad in (None, "2", True, 2.0):
            with self.subTest(rows=bad):
                a, b = self.tmp / "a.jsonl", self.tmp / "b.jsonl"
                for p, rater in ((a, "alice"), (b, "bob")):
                    head = {"schema": rate.SHEET_SCHEMA, "rater": rater, "dimension": "pacing",
                            "rubric_digest": "R", "draft_digest": "D"}
                    if bad is not None:
                        head["rows"] = bad
                    body = "\n".join(json.dumps({"rater": rater, "draft": d, "criterion": c,
                                                 "score": s, "max_score": t})
                                     for d, c, s, t in rows)
                    p.write_text(json.dumps({"_sheet": head}) + "\n" + body + "\n")
                with self.assertRaises(SystemExit) as e:
                    rate.main(["score", str(a), str(b)])
                self.assertIn("`rows` must be an integer", str(e.exception))

if __name__ == "__main__":
    unittest.main()
