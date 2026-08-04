"""WI-3 — behavior-lock for the BM25 card retriever (`query.search`).

A behavior-lock, not a re-derivation: pinned numeric score vectors (captured from the shipped
ranker) plus the invariants that make BM25 worth having — length normalization, IDF ≥ 0 at df==N,
rare-term dominance, deterministic ties, and the empty/limit/total contract. If any of these change,
retrieval regressed. We do NOT recompute the formula in-test (that would repeat any bug); we assert
stored numbers and observable behavior.
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from klode import lib                                                     # noqa: E402
from klode.lib.query import search                                       # noqa: E402


def _kb(root: Path, cards: dict) -> "lib.Config":
    (root / "library" / "cards").mkdir(parents=True, exist_ok=True)
    (root / "library" / "books").mkdir(exist_ok=True)
    (root / "library.toml").write_text(
        '[library]\ndir = "library"\ncards = "cards"\nshelves = ["books"]\n'
        '[bibliography]\nenabled = false\n', encoding="utf-8")
    for cid, thin in cards.items():
        (root / "library" / "cards" / f"{cid}.md").write_text(
            f"---\nid: {cid}\nshelf: books\nzoom: full\n---\n# {cid}\n\n## Thin\n{thin}\n", encoding="utf-8")
    (root / "library" / "cards" / "INDEX.md").write_text("# Card Index\n", encoding="utf-8")
    return lib.Config.load(root / "library.toml")


class BM25Lock(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-bm25-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_pinned_score_vector(self):
        # captured from the shipped ranker — any change to k1/b/IDF/length-norm/tf trips this
        cfg = _kb(self.tmp, {"aaa": "pacing", "bbb": "pacing " + "filler " * 30})
        hits, total = search(cfg, ["pacing"])
        self.assertEqual(total, 2)
        by = {h.id: h.score for h in hits}
        self.assertAlmostEqual(by["aaa"], 0.2917144909, places=6)
        self.assertAlmostEqual(by["bbb"], 0.1325974959, places=6)

    def test_short_on_point_outranks_long_padded_at_equal_tf(self):
        # length normalization: same tf=1 for 'pacing', but bbb is padded long -> short aaa wins
        cfg = _kb(self.tmp, {"aaa": "pacing", "bbb": "pacing " + "filler " * 30})
        hits, _ = search(cfg, ["pacing"])
        self.assertEqual(hits[0].id, "aaa")

    def test_idf_non_negative_when_term_in_every_doc(self):
        # df==N -> the log(1+·)/+0.5 smoothing keeps IDF >= 0, so every score stays positive
        cfg = _kb(self.tmp, {"aaa": "common word", "bbb": "common word too", "ccc": "common again"})
        hits, _ = search(cfg, ["common"])
        self.assertEqual(len(hits), 3)
        self.assertTrue(all(h.score > 0 for h in hits))

    def test_rare_term_dominates_common_term(self):
        # 'rhythm' in 1 of 3 (rare), 'pacing' in all 3 (common); on the card holding both, the rare
        # term's IDF makes its single-term score far exceed the common term's
        cfg = _kb(self.tmp, {"aaa": "rhythm pacing", "bbb": "pacing here", "ccc": "pacing there"})
        rare = {h.id: h.score for h in search(cfg, ["rhythm"])[0]}["aaa"]
        common = {h.id: h.score for h in search(cfg, ["pacing"])[0]}["aaa"]
        self.assertGreater(rare, common)

    def test_equal_scores_tie_break_by_id(self):
        cfg = _kb(self.tmp, {"bbb": "identical blob", "aaa": "identical blob"})
        hits, _ = search(cfg, ["identical"])
        self.assertEqual([h.id for h in hits], ["aaa", "bbb"])       # ascending id under equal score

    def test_empty_and_whitespace_terms(self):
        cfg = _kb(self.tmp, {"aaa": "pacing"})
        self.assertEqual(search(cfg, []), ([], 0))
        self.assertEqual(search(cfg, ["  ", "\t"]), ([], 0))

    def test_non_matching_term_and_limit_total(self):
        cfg = _kb(self.tmp, {f"c{i}": "pacing matters" for i in range(5)})
        hits, total = search(cfg, ["pacing", "zzznomatch"], limit=2)   # non-matching term contributes 0
        self.assertEqual(len(hits), 2)
        self.assertEqual(total, 5)

    def test_zero_dep(self):
        code = ("import sys, klode.lib; bad=[m for m in sys.modules if m.split('.')[0] in "
                "{'numpy','scipy','tiktoken','torch','requests','pydantic'}]; print('DIRTY' if bad else 'CLEAN')")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO))
        self.assertEqual(out.stdout.strip(), "CLEAN", out.stderr)


if __name__ == "__main__":
    unittest.main()
