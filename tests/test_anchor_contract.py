"""WI-1 — one structured anchor contract shared by the linter and the gate.

The linter already parses the full SPEC anchor grammar (`common.parse_markers` → `Marker`:
phrase, regex flag, before/after context, `#n` occurrence, `;`/`|` multi-anchor). The gate used
a narrower regex that captured only bare phrases, so a criterion could not use the disambiguation
the linter supports, and the two parsers could drift. These tests pin: the parser is exposed on the
facade, the gate consumes it, grounding honours the full Marker, and legacy anchors are unchanged.
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
from klode.gate import load_criteria, ground                             # noqa: E402

FIX = REPO / "tests" / "fixtures" / "kb-fixture" / "library.toml"         # dim "pacing"


def _make_kb(root: Path, *, moves, source_text, cid="src1", dim="craft") -> Path:
    lib_dir = root / "library"
    (lib_dir / "books").mkdir(parents=True, exist_ok=True)
    (lib_dir / "cards").mkdir(exist_ok=True)
    (lib_dir / "frameworks" / "_syntheses").mkdir(parents=True, exist_ok=True)
    (root / "library.toml").write_text(
        '[library]\ndir = "library"\ncards = "cards"\nshelves = ["books"]\n'
        '[bibliography]\nenabled = false\n'
        '[frameworks]\nenabled = true\ndir = "frameworks"\nsyntheses = "_syntheses"\n', encoding="utf-8")
    (lib_dir / "books" / f"{cid}.txt").write_text(source_text, encoding="utf-8")
    (lib_dir / "cards" / f"{cid}.md").write_text(
        f"---\nid: {cid}\nshelf: books\nfile: library/books/{cid}.txt\ngrep_ready: true\n---\n# {cid}\n",
        encoding="utf-8")
    (lib_dir / "cards" / "INDEX.md").write_text(f"# Card Index\n\n- [{cid}]({cid}.md)\n", encoding="utf-8")
    (lib_dir / "frameworks" / "_syntheses" / f"{dim}.md").write_text(
        f"---\ntitle: {dim}\nstatus: canonical\ndimension: {dim}\ncards: [{cid}]\n---\n\n# {dim}\n\n"
        f"**Core question:** q?\n\n## Craft\n\nintro.\n\n{moves}\n", encoding="utf-8")
    return root / "library.toml"


class AnchorParsing(unittest.TestCase):
    def test_bare_phrase_is_a_single_literal_marker(self):
        ms = lib.parse_markers("- **Move.** (grep: `the exact phrase`).")
        self.assertEqual(len(ms), 1)
        m = ms[0]
        self.assertEqual((m.phrase, m.regex, m.before, m.after, m.nth),
                         ("the exact phrase", False, None, None, None))

    def test_multi_anchor_captures_every_phrase(self):
        self.assertEqual([m.phrase for m in lib.parse_markers("(grep: `A`; `B` | `C`)")], ["A", "B", "C"])
        # keyed form: each phrase re-states the key
        self.assertEqual([m.phrase for m in lib.parse_markers("(`grep: \"A\"`; `grep: \"B\"`)")], ["A", "B"])

    def test_context_and_occurrence_selectors(self):
        m = lib.parse_markers("(grep: `phrase` before `lead words` after `tail words`)")[0]
        self.assertEqual((m.before, m.after, m.nth), ("lead words", "tail words", None))
        m2 = lib.parse_markers("(grep: `phrase` #2)")[0]
        self.assertEqual((m2.before, m2.after, m2.nth), (None, None, 2))

    def test_regex_flag_only_on_the_re_form(self):
        self.assertTrue(lib.parse_markers("(grep-re: `a.b+c`)")[0].regex)
        self.assertFalse(lib.parse_markers("(grep: `a.b+c`)")[0].regex)

    def test_deterministic_parse(self):
        s = "(grep: `quickens the pace`), (grep: `slows the tempo` #2)"
        self.assertEqual(lib.parse_markers(s), lib.parse_markers(s))

    def test_marker_is_exposed_on_the_facade(self):
        self.assertIn("Marker", lib.__all__)
        self.assertIn("parse_markers", lib.__all__)


class GateConsumesTheSharedParser(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-anchor-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_gate_markers_equal_the_linter_parse_for_the_same_bullet(self):
        # the gate must carry the SAME structured Markers the linter would parse — no narrower regex
        bullet = "- **Move.** (grep: `quickens the pace`), (grep: `slows the tempo`)"
        cfg = lib.Config.load(_make_kb(self.tmp, moves=bullet,
                                       source_text="prose quickens the pace and later slows the tempo end"))
        crit = load_criteria(cfg, "craft")[0][0]
        self.assertEqual(list(crit.markers), lib.parse_markers(bullet))
        self.assertEqual([m.phrase for m in crit.markers], ["quickens the pace", "slows the tempo"])

    def test_legacy_bare_anchors_still_ground_unchanged(self):
        cfg = lib.Config.load(FIX)
        crit, panel = load_criteria(cfg, "pacing")
        self.assertEqual(len(crit), 3)
        for c in crit:
            self.assertTrue(ground(cfg, c, panel).grounded, c.statement)

    def test_grounding_honours_the_occurrence_selector(self):
        # a `#2` anchor grounds only when the phrase occurs at least twice — a capability the old
        # phrase-only gate could not express. Threading the full Marker through grounding enables it.
        src = "the beat repeats. the beat repeats again."
        cfg2 = lib.Config.load(_make_kb(self.tmp, cid="c2", dim="d2",
                                        moves="- **Twice.** (grep: `the beat repeats` #2)", source_text=src))
        c = load_criteria(cfg2, "d2")[0][0]
        self.assertTrue(ground(cfg2, c, load_criteria(cfg2, "d2")[1]).grounded)   # 2 occurrences → #2 resolves
        # and #3 does NOT resolve (only two occurrences)
        cfg3 = lib.Config.load(_make_kb(Path(tempfile.mkdtemp()), cid="c3", dim="d3",
                                        moves="- **Thrice.** (grep: `the beat repeats` #3)", source_text=src))
        g = ground(cfg3, load_criteria(cfg3, "d3")[0][0], load_criteria(cfg3, "d3")[1])
        self.assertFalse(g.grounded)

    def test_anchorless_move_still_fails_loud(self):
        cfg = lib.Config.load(_make_kb(self.tmp, cid="cx", dim="dx",
                                       moves="- **Naked move.** prose but no anchor", source_text="x"))
        with self.assertRaises(ValueError):
            load_criteria(cfg, "dx")

    def test_unknown_dimension_raises(self):
        cfg = lib.Config.load(FIX)
        with self.assertRaises(ValueError):
            load_criteria(cfg, "no-such-dimension")

    def test_craft_with_no_moves_raises(self):
        # a Craft layer with prose but NO bold-move bullets yields no criteria — fail loud
        cfg = lib.Config.load(_make_kb(self.tmp, cid="cp", dim="dp",
                                       moves="just prose, no move bullets here", source_text="x"))
        with self.assertRaises(ValueError):
            load_criteria(cfg, "dp")

    def test_panel_skips_empty_entries(self):
        from klode.gate.criteria import _panel
        self.assertEqual(_panel("a, , b"), ["a", "b"])            # an empty segment is skipped

    def test_occurrence_selector_locates_the_nth_line_not_the_first(self):
        src = "intro\nthe target phrase\nfiller line\nthe target phrase\nend"   # occurs on lines 2 and 4
        cfg = lib.Config.load(_make_kb(self.tmp, cid="cn", dim="dn",
                                       moves="- **Second.** (grep: `the target phrase` #2)", source_text=src))
        m = load_criteria(cfg, "dn")[0][0].markers[0]
        ev = lib.verify_evidence(cfg, "cn", m)
        self.assertEqual(ev.resolution, lib.EvidenceResolution.FOUND)
        self.assertEqual([n for n, _ in ev.lines], [4])          # the SECOND occurrence, not the first

    def test_regex_selector_locates_via_pattern(self):
        cfg = lib.Config.load(_make_kb(self.tmp, cid="cr", dim="dr",
                                       moves="- **Rx.** (grep-re: `t.rget`)", source_text="pre\ntarget here\npost"))
        ev = lib.verify_evidence(cfg, "cr", load_criteria(cfg, "dr")[0][0].markers[0])
        self.assertEqual(ev.resolution, lib.EvidenceResolution.FOUND)
        self.assertEqual([n for n, _ in ev.lines], [2])

    def test_empty_phrase_or_regex_does_not_ground(self):
        cfg = lib.Config.load(_make_kb(self.tmp, cid="ce", dim="de",
                                       moves="- **M.** (grep: `present`)", source_text="present here"))
        for bad in ("", "   ", lib.Marker("", regex=True), lib.Marker("  ")):
            self.assertEqual(lib.verify_evidence(cfg, "ce", bad).resolution,
                             lib.EvidenceResolution.NOT_FOUND)

    def test_zeroth_occurrence_does_not_ground(self):
        cfg = lib.Config.load(_make_kb(self.tmp, cid="cz", dim="dz",
                                       moves="- **M.** (grep: `x`)", source_text="x here"))
        self.assertEqual(lib.verify_evidence(cfg, "cz", lib.Marker("x", nth=0)).resolution,
                         lib.EvidenceResolution.NOT_FOUND)

    def test_divergent_phrases_and_markers_are_rejected(self):
        from klode.gate import Criterion
        with self.assertRaises(ValueError):                      # a fabricated phrase paired with a real marker
            Criterion("X", "s", ("missing",), markers=(lib.Marker("real"),))
        with self.assertRaises(ValueError):                      # non-Marker element
            Criterion("Y", "s", ("p",), markers=("not-a-marker",))

    def test_advisory_inside_a_regex_anchor_does_not_flip_criticality(self):
        cfg = lib.Config.load(_make_kb(self.tmp, cid="ca", dim="da",
                                       moves="- **M.** (grep-re: `[advisory]item`)", source_text="Xitem here"))
        self.assertEqual(load_criteria(cfg, "da")[0][0].criticality, "required")


class ZeroDep(unittest.TestCase):
    def test_import_klode_lib_stays_stdlib_only(self):
        code = ("import sys, klode.lib; "
                "bad=[m for m in sys.modules if m.split('.')[0] in "
                "{'numpy','scipy','tiktoken','torch','requests','pydantic','sklearn'}]; "
                "print('DIRTY' if bad else 'CLEAN')")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             cwd=str(REPO))
        self.assertEqual(out.stdout.strip(), "CLEAN", out.stderr)


if __name__ == "__main__":
    unittest.main()
