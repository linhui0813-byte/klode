"""WI-2 — verified-context bundle, fail-CLOSED.

`build_context_bundle` partitions `(card, anchor)` requests into `grounded` (verified spans with
provenance) and `rejected` (explicit resolution). Nothing is silently dropped; there is NO
generation. The grounding path is real on a tmpdir KB; `today=` is injected for freshness.
"""
import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import fields
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from klode import lib                                                     # noqa: E402

R = lib.EvidenceResolution


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _kb(root: Path, sources: dict, *, stamp=(), review_by=None) -> "lib.Config":
    review_by = review_by or {}
    lib_dir = root / "library"
    (lib_dir / "books").mkdir(parents=True, exist_ok=True)
    (lib_dir / "cards").mkdir(exist_ok=True)
    (root / "library.toml").write_text(
        '[library]\ndir = "library"\ncards = "cards"\nshelves = ["books"]\n[bibliography]\nenabled = false\n',
        encoding="utf-8")
    idx = ["# Card Index\n"]
    for cid, text in sources.items():
        (lib_dir / "books" / f"{cid}.txt").write_text(text, encoding="utf-8")
        fm = [f"id: {cid}", "shelf: books", f"file: library/books/{cid}.txt", "grep_ready: true"]
        if cid in stamp:
            fm.append(f"source_sha256: {_sha(text)}")
        if cid in review_by:
            fm.append(f"review_by: {review_by[cid]}")
        (lib_dir / "cards" / f"{cid}.md").write_text(
            "---\n" + "\n".join(fm) + f"\n---\n# {cid}\n\n## Content\n`library/books/{cid}.txt`\n", encoding="utf-8")
        idx.append(f"- [{cid}]({cid}.md)")
    (lib_dir / "cards" / "INDEX.md").write_text("\n".join(idx) + "\n", encoding="utf-8")
    return lib.Config.load(root / "library.toml")


class ContextBundle(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-bundle-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_mixed_grounded_and_rejected_in_one_call(self):
        cfg = _kb(self.tmp, {"a": "the anchor phrase appears here"})
        b = lib.build_context_bundle(cfg, [("a", "the anchor phrase"), ("a", "no such zzqx phrase")])
        self.assertEqual(len(b.grounded), 1)
        self.assertTrue(b.grounded[0].usable)
        self.assertIn("the anchor phrase", b.grounded[0].text)
        self.assertEqual([(r.card, r.phrase, r.resolution) for r in b.rejected],
                         [("a", "no such zzqx phrase", R.NOT_FOUND)])

    def test_grounded_text_equals_source_window(self):
        cfg = _kb(self.tmp, {"a": "line one\nTHE ANCHOR here\nline three"})
        e = lib.build_context_bundle(cfg, [("a", "THE ANCHOR")], context_lines=0).grounded[0]
        want = "\n".join("line one\nTHE ANCHOR here\nline three".splitlines()[e.line_start - 1:e.line_end])
        self.assertEqual(e.text, want)

    def test_stale_source_is_rejected_not_dropped(self):
        cfg = _kb(self.tmp, {"a": "anchor here"}, stamp={"a"})
        (self.tmp / "library" / "books" / "a.txt").write_text("anchor here plus drift", encoding="utf-8")
        b = lib.build_context_bundle(cfg, [("a", "anchor here")])
        self.assertEqual(b.grounded, ())
        self.assertEqual(b.rejected[0].resolution, R.SOURCE_STALE)   # rejected, with the reason — not dropped

    def test_review_expired_is_rejected_but_grounds_earlier(self):
        cfg = _kb(self.tmp, {"a": "anchor here"}, stamp={"a"}, review_by={"a": "2020-01-01"})
        rej = lib.build_context_bundle(cfg, [("a", "anchor here")], today=date(2026, 1, 1))
        self.assertEqual(rej.rejected[0].resolution, R.REVIEW_EXPIRED)
        ok = lib.build_context_bundle(cfg, [("a", "anchor here")], today=date(2019, 1, 1))
        self.assertEqual(len(ok.grounded), 1)                        # same anchor, earlier date -> grounds

    def test_provenance_on_a_grounded_entry(self):
        text = "before\nthe cited anchor line\nafter"
        cfg = _kb(self.tmp, {"a": text})
        e = lib.build_context_bundle(cfg, [("a", "the cited anchor")]).grounded[0]
        self.assertEqual(e.phrase, "the cited anchor")
        self.assertEqual(e.card, "a")
        self.assertTrue(e.match_lines)
        self.assertEqual(e.source_sha, _sha(text))                   # the sha actually grounded against

    def test_no_unverified_text_in_grounded(self):
        cfg = _kb(self.tmp, {"a": "present"})
        b = lib.build_context_bundle(cfg, [("a", "present"), ("a", "absent")])
        self.assertTrue(all(e.usable and e.resolution in (R.FOUND, R.FOLDED_ONLY) for e in b.grounded))

    def test_bundle_has_no_generation_field(self):
        # context-only by contract: a generator/completion must NOT exist on this type
        names = {f.name for f in fields(lib.ContextBundle)}
        self.assertEqual(names, {"grounded", "rejected"})
        self.assertNotIn("completion", names)

    def test_same_card_requests_share_one_snapshot(self):
        cfg = _kb(self.tmp, {"a": "anchor one and anchor two here"})
        b = lib.build_context_bundle(cfg, [("a", "anchor one"), ("a", "anchor two")])
        self.assertEqual(len({e.source_sha for e in b.grounded}), 1)  # one consistent snapshot

    def test_deterministic(self):
        cfg = _kb(self.tmp, {"a": "anchor here"})
        reqs = [("a", "anchor here"), ("a", "missing")]
        self.assertEqual(lib.build_context_bundle(cfg, reqs, today=date(2026, 1, 1)),
                         lib.build_context_bundle(cfg, reqs, today=date(2026, 1, 1)))

    def test_empty_phrase_is_rejected_not_grounded(self):
        cfg = _kb(self.tmp, {"a": "some content here"})
        b = lib.build_context_bundle(cfg, [("a", "")])
        self.assertEqual(b.grounded, ())
        self.assertEqual(b.rejected[0].resolution, R.NOT_FOUND)

    def test_occurrence_marker_bundle_locates_the_nth(self):
        cfg = _kb(self.tmp, {"a": "l1\nthe mark\nl3\nthe mark\nl5"})     # 'the mark' on lines 2 and 4
        b = lib.build_context_bundle(cfg, [("a", lib.Marker("the mark", nth=2))], context_lines=0)
        self.assertEqual(len(b.grounded), 1)
        self.assertEqual(b.grounded[0].match_lines, (4,))               # the SECOND occurrence, not the first

    def test_facade_and_zero_dep(self):
        for name in ("build_context_bundle", "ContextBundle", "RejectedContext"):
            self.assertIn(name, lib.__all__)
        code = ("import sys, klode.lib; bad=[m for m in sys.modules if m.split('.')[0] in "
                "{'numpy','tiktoken','torch','requests','pydantic'}]; print('DIRTY' if bad else 'CLEAN')")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO))
        self.assertEqual(out.stdout.strip(), "CLEAN", out.stderr)


if __name__ == "__main__":
    unittest.main()
