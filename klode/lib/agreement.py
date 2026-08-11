"""Extraction agreement — how far a candidate extraction departs from a control, measured in three
independent ways that fail differently.

**This is telemetry, not a verdict.** It answers *"do these two differ, and how?"* — never *"which
one is right"*. The control (`pdftotext`) is the baseline precisely when escalation has already
judged its text layer unreliable, so a correct OCR reconstruction can disagree with it loudly and
still be the better text. Fidelity to the rendered page needs `visual.py`; this module cannot
supply it and does not pretend to.

Three signals, because they catch disjoint failures and none subsumes another:

| signal | catches | blind to |
|---|---|---|
| `containment` | dropped pages / lost material | reordering, duplication |
| `inflation` | duplicated tables, repeated headers | reordering, loss |
| per-window `order` | reading-order scrambling | loss, duplication |

**Order is measured per window, never book-wide.** A whole-book rank correlation is the wrong
instrument: 300 pages of 400 tokens with *every page internally reversed* scores ρ = 0.999978 —
invisible — while relocating the first 1% of a document scores 0.9406, a loud alarm for text whose
local order is 99% intact. Both numbers are reproduced in `tests/test_agreement.py`. Windowing
inverts that sensitivity: per-page reversal drives every window to ≈ −1, and a relocated block
leaves every window's internal order intact, which is the honest reading of both events.

Pure and stdlib-only: no I/O, no backend, no subprocess.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

__all__ = ["tokenize", "Window", "Agreement", "compare", "DEFAULT_WINDOW", "MIN_ANCHORS"]

DEFAULT_WINDOW = 400        # tokens — roughly one printed page, the unit layout models scramble
MIN_ANCHORS = 8             # per window: below this, order is not measured, it is abstained on

_DEHYPHEN = re.compile(r"-\s*")
_NONWORD = re.compile(r"[^0-9a-z]+")


def tokenize(text: str) -> list[str]:
    """The stated tokenizer. Every downstream number depends on it, so it is fixed here and tested:

    1. Unicode **NFKC** — ligatures and full-width forms collapse to their plain equivalents.
    2. **De-hyphenation** of `-` plus any following whitespace, so a line-wrapped `informa-\\ntion`
       is one token. This mirrors `common._dehyphenate`, the rule the citation linter already uses;
       diverging here would mean the two halves of klode disagree about what a word is.
    3. **Casefold**, then split on any run of non-alphanumerics.

    Punctuation and layout whitespace carry no evidence about extraction fidelity and vary freely
    between backends, so they are removed rather than compared.
    """
    t = unicodedata.normalize("NFKC", text)
    t = _DEHYPHEN.sub("", t)
    return [w for w in _NONWORD.split(t.casefold()) if w]


@dataclass(frozen=True)
class Window:
    """One window of the control, and how its tokens sit in the candidate."""
    index: int
    start: int                  # token offset in the control
    anchors: int                # tokens unique in BOTH texts — what `order` rests on
    order: float | None         # rank correlation within this window; None = abstained

    @property
    def abstained(self) -> bool:
        return self.order is None


@dataclass(frozen=True)
class Agreement:
    containment: float          # |bag(control) ∩ bag(candidate)| / |bag(control)|
    inflation: float            # |candidate| / |control| — >1 means material was duplicated
    control_tokens: int
    candidate_tokens: int
    windows: tuple[Window, ...]

    @property
    def measured(self) -> tuple[float, ...]:
        return tuple(w.order for w in self.windows if w.order is not None)

    @property
    def abstained_windows(self) -> int:
        return sum(1 for w in self.windows if w.abstained)

    def quantile(self, q: float) -> float | None:
        """Nearest-rank quantile of the measured window orders, or None when nothing was measured.
        A distribution is reported because one bad page in three hundred is a real finding that a
        mean would bury."""
        vals = sorted(self.measured)
        if not vals:
            return None
        idx = max(0, min(len(vals) - 1, int(round(q * (len(vals) - 1)))))
        return vals[idx]

    @property
    def worst_window(self) -> Window | None:
        scored = [w for w in self.windows if w.order is not None]
        return min(scored, key=lambda w: w.order) if scored else None


def _spearman(xs: list[int], ys: list[int]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
    return (num / den) if den else None


def compare(control: str, candidate: str, *, window: int = DEFAULT_WINDOW,
            min_anchors: int = MIN_ANCHORS) -> Agreement:
    """Compare a candidate extraction against a control. See the module docstring for what this
    does and does not establish."""
    if window < 2:
        raise ValueError(f"window must be >= 2 tokens, got {window}")
    a, b = tokenize(control), tokenize(candidate)
    ca, cb = Counter(a), Counter(b)

    overlap = sum(min(n, cb[t]) for t, n in ca.items())
    containment = (overlap / len(a)) if a else 0.0
    inflation = (len(b) / len(a)) if a else 0.0

    # Anchors: tokens occurring exactly once in BOTH texts. A repeated token has no single position,
    # so it cannot say anything about order. Duplication drives this set toward empty — which is
    # why `inflation` exists as a separate signal and order abstains rather than guessing.
    anchor_set = {t for t, n in ca.items() if n == 1 and cb.get(t) == 1}
    pos_b = {t: i for i, t in enumerate(b) if t in anchor_set}

    windows: list[Window] = []
    for wi, start in enumerate(range(0, len(a), window)):
        chunk = [(i, t) for i, t in enumerate(a[start:start + window], start=start)
                 if t in anchor_set]
        if len(chunk) < min_anchors:
            windows.append(Window(wi, start, len(chunk), None))
            continue
        # rank WITHIN the window on both sides: this asks "did this page keep its internal
        # order", independent of where the page as a whole landed in the candidate
        xs = list(range(len(chunk)))
        by_b = sorted(range(len(chunk)), key=lambda k: pos_b[chunk[k][1]])
        ys = [0] * len(chunk)
        for rank, k in enumerate(by_b):
            ys[k] = rank
        windows.append(Window(wi, start, len(chunk), _spearman(xs, ys)))

    return Agreement(containment=containment, inflation=inflation,
                     control_tokens=len(a), candidate_tokens=len(b), windows=tuple(windows))
