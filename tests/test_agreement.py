"""WI-1 — the agreement primitives, and the two permutation tests that killed v1's metric.

The plan's acceptance criteria, verbatim: *"reversing every page must fail while whole-book page
order is intact; moving one block must not dominate."* A whole-book rank correlation gets both
backwards, and both numbers are reproduced here so the regression is impossible to reintroduce
quietly.
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from klode.lib.agreement import (Agreement, DEFAULT_WINDOW, MIN_ANCHORS,  # noqa: E402
                                 compare, tokenize)

PAGE = 400


def _doc(n: int) -> list[str]:
    """n distinct tokens — every one an anchor, so order is measured rather than abstained on."""
    return [f"tok{i}" for i in range(n)]


def _book_spearman(order: list[int]) -> float:
    """The v1 metric: one rank correlation over the whole book. Kept ONLY to demonstrate why it
    was replaced — it is not used by the implementation."""
    n = len(order)
    pos = {tok: i for i, tok in enumerate(order)}
    xs, ys = list(range(n)), [pos[i] for i in range(n)]
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
    return num / den


class TheMetricV1Used(unittest.TestCase):
    """Why book-wide Spearman had to go. These assertions are about the OLD metric."""

    def test_it_is_blind_to_every_page_being_reversed(self):
        n = 120_000
        scrambled = []
        for i in range(0, n, PAGE):
            scrambled.extend(reversed(range(i, min(i + PAGE, n))))
        rho = _book_spearman(scrambled)
        self.assertGreater(rho, 0.9999)          # 0.999978 — indistinguishable from perfect
        self.assertAlmostEqual(rho, 0.999978, places=5)

    def test_it_overreacts_to_one_relocated_block(self):
        n = 120_000
        moved = list(range(n // 100, n)) + list(range(n // 100))
        rho = _book_spearman(moved)
        self.assertLess(rho, 0.95)               # 0.9406 — loud, for 99%-intact local order
        self.assertAlmostEqual(rho, 0.9406, places=3)


class WindowLocalOrder(unittest.TestCase):
    """The replacement, held to the plan's two criteria."""

    def test_reversing_every_page_is_caught(self):
        a = _doc(4000)
        b = []
        for i in range(0, len(a), PAGE):
            b.extend(reversed(a[i:i + PAGE]))
        r = compare(" ".join(a), " ".join(b), window=PAGE)
        self.assertEqual(r.abstained_windows, 0)
        self.assertAlmostEqual(r.containment, 1.0)      # nothing lost...
        self.assertAlmostEqual(r.inflation, 1.0)        # ...and nothing added
        self.assertLess(r.quantile(0.95), -0.99)        # every window inverted
        self.assertLess(r.worst_window.order, -0.99)

    def test_one_relocated_block_does_not_dominate(self):
        # "Must not dominate" is not "must not show". A moved block SHOULD register in the window
        # that straddles it — suppressing that would be a blind spot, not a virtue. What must not
        # happen is the whole distribution collapsing, which is what book-wide Spearman did
        # (0.9406 for text whose local order was 99% intact).
        a = _doc(4000)
        cut = len(a) // 100
        b = a[cut:] + a[:cut]                            # first 1% moved to the end
        r = compare(" ".join(a), " ".join(b), window=PAGE)
        disturbed = [w for w in r.windows if w.order < 0.99]
        self.assertEqual(len(disturbed), 1, "only the straddling window should move")
        self.assertEqual(disturbed[0].index, 0)
        self.assertGreater(r.quantile(0.50), 0.99)       # the distribution is NOT dominated
        self.assertAlmostEqual(r.containment, 1.0)       # and nothing was actually lost

    def test_relocation_and_scrambling_are_distinguishable(self):
        # the pair the metric exists to tell apart: one bad window vs every window bad
        a = _doc(4000)
        moved = a[40:] + a[:40]
        scrambled = []
        for i in range(0, len(a), PAGE):
            scrambled.extend(reversed(a[i:i + PAGE]))
        rm = compare(" ".join(a), " ".join(moved), window=PAGE)
        rs = compare(" ".join(a), " ".join(scrambled), window=PAGE)
        bad = lambda r: sum(1 for w in r.windows if w.order < 0.99)   # noqa: E731
        self.assertEqual((bad(rm), bad(rs)), (1, 10))
        self.assertGreater(rm.quantile(0.50), 0.99)
        self.assertLess(rs.quantile(0.50), -0.99)

    def test_a_single_bad_page_is_not_averaged_away(self):
        # one scrambled page in ten is a real finding a mean would bury; the distribution keeps it
        a = _doc(4000)
        b = list(a)
        b[PAGE * 3:PAGE * 4] = list(reversed(b[PAGE * 3:PAGE * 4]))
        r = compare(" ".join(a), " ".join(b), window=PAGE)
        self.assertGreater(r.quantile(0.50), 0.99)       # the median page is fine
        self.assertLess(r.worst_window.order, -0.99)     # and the bad one is still visible
        self.assertEqual(r.worst_window.index, 3)

    def test_clean_text_agrees_with_itself(self):
        a = " ".join(_doc(2000))
        r = compare(a, a, window=PAGE)
        self.assertAlmostEqual(r.containment, 1.0)
        self.assertAlmostEqual(r.inflation, 1.0)
        self.assertTrue(all(w.order > 0.999 for w in r.windows if w.order is not None))


class ContainmentAndInflation(unittest.TestCase):
    """The two signals that survived the audit unchanged."""

    def test_dropped_material_shows_in_containment(self):
        a = _doc(4000)
        r = compare(" ".join(a), " ".join(a[:2000]), window=PAGE)
        self.assertAlmostEqual(r.containment, 0.5, places=3)
        self.assertAlmostEqual(r.inflation, 0.5, places=3)

    def test_duplicated_material_shows_in_inflation(self):
        a = " ".join(_doc(2000))
        r = compare(a, a + " " + a, window=PAGE)
        self.assertAlmostEqual(r.containment, 1.0)
        self.assertAlmostEqual(r.inflation, 2.0, places=3)

    def test_duplication_collapses_the_anchor_set_so_order_abstains(self):
        # every token now occurs twice, so none is unique-in-both: order has nothing to stand on
        # and must abstain rather than emit a number. `inflation` is the signal that matters here.
        a = " ".join(_doc(2000))
        r = compare(a, a + " " + a, window=PAGE)
        self.assertEqual(r.measured, ())
        self.assertEqual(r.abstained_windows, len(r.windows))
        self.assertIsNone(r.quantile(0.5))
        self.assertIsNone(r.worst_window)

    def test_the_three_signals_separate_the_four_failure_modes(self):
        a = _doc(4000)
        scrambled = []
        for i in range(0, len(a), PAGE):
            scrambled.extend(reversed(a[i:i + PAGE]))
        cases = {
            "clean": a,
            "scrambled": scrambled,
            "dropped": a[:2000],
            "duplicated": a + a,
        }
        got = {}
        for name, b in cases.items():
            r = compare(" ".join(a), " ".join(b), window=PAGE)
            got[name] = (round(r.containment, 2), round(r.inflation, 2),
                         None if r.quantile(0.5) is None else round(r.quantile(0.5), 2))
        self.assertEqual(got["clean"], (1.0, 1.0, 1.0))
        self.assertEqual(got["scrambled"], (1.0, 1.0, -1.0))     # only order moves
        self.assertEqual(got["dropped"], (0.5, 0.5, 1.0))        # only containment moves
        self.assertEqual(got["duplicated"], (1.0, 2.0, None))    # only inflation moves
        self.assertEqual(len(set(got.values())), 4)              # all four are distinguishable


class Tokenizer(unittest.TestCase):
    """Stated, because every number above depends on it and the audit called it unspecified."""

    def test_nfkc_normalization(self):
        self.assertEqual(tokenize("ﬁle"), ["file"])              # ligature
        self.assertEqual(tokenize("ｆｕｌｌ"), ["full"])            # full-width

    def test_dehyphenation_matches_the_citation_linter(self):
        # `common._dehyphenate` folds `-` plus following whitespace; diverging here would make the
        # two halves of klode disagree about what a word is
        self.assertEqual(tokenize("informa-\ntion"), ["information"])
        self.assertEqual(tokenize("re-sign"), ["resign"])

    def test_case_and_punctuation_are_removed(self):
        self.assertEqual(tokenize("The, QUICK. brown!"), ["the", "quick", "brown"])

    def test_layout_whitespace_is_not_evidence(self):
        self.assertEqual(tokenize("a   b\n\n\tc"), ["a", "b", "c"])

    def test_empty_and_punctuation_only_input(self):
        self.assertEqual(tokenize(""), [])
        self.assertEqual(tokenize("!!! ... ---"), [])


class Abstention(unittest.TestCase):
    def test_too_few_anchors_abstains_rather_than_guessing(self):
        a = " ".join(["same"] * 500)                              # no unique tokens at all
        r = compare(a, a, window=PAGE)
        self.assertEqual(r.measured, ())
        self.assertTrue(all(w.abstained for w in r.windows))

    def test_the_anchor_floor_is_honoured(self):
        a = _doc(MIN_ANCHORS - 1)
        r = compare(" ".join(a), " ".join(a), window=PAGE)
        self.assertTrue(all(w.abstained for w in r.windows))
        b = _doc(MIN_ANCHORS)
        r2 = compare(" ".join(b), " ".join(b), window=PAGE)
        self.assertFalse(r2.windows[0].abstained)

    def test_empty_control_does_not_divide_by_zero(self):
        r = compare("", "anything at all here", window=PAGE)
        self.assertEqual(r.containment, 0.0)
        self.assertEqual(r.inflation, 0.0)

    def test_a_degenerate_window_size_is_refused(self):
        with self.assertRaises(ValueError):
            compare("a b c", "a b c", window=1)


if __name__ == "__main__":
    unittest.main()
