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

**Order is measured per window, never book-wide.** When the caller supplies real page boundaries
(`compare(..., control_pages=[...])`) the windows ARE pages. Without them the window is a
fixed-size *proxy* for a page, and the guarantee is correspondingly weaker: a document whose pages
are much shorter than the window can have every page internally reversed while each window — which
straddles several pages — still scores near 1.0. `pdftotext` gives page boundaries for free via
form feeds, so the pipeline passes them; the fixed-window path is the fallback, not the promise.

A whole-book rank correlation is the wrong instrument either way: 300 pages of 400 tokens with
*every page internally reversed* scores ρ = 0.999978 — invisible — while relocating the first 1% of
a document scores 0.9406, a loud alarm for text whose local order is 99% intact. Both numbers are
reproduced in `tests/test_agreement.py`. Windowing inverts that sensitivity: per-page reversal
drives every window to ≈ −1, and a relocated block leaves every window's internal order intact.

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


def tokenize(text: str) -> list[str]:
    """The stated tokenizer. Every downstream number depends on it, so it is fixed here and tested:

    1. Unicode **NFKC** — ligatures and full-width forms collapse to their plain equivalents.
    2. **De-hyphenation** of `-` plus any following whitespace, so a line-wrapped `informa-\\ntion`
       is one token. This mirrors `common._dehyphenate`, the rule the citation linter already uses;
       diverging here would mean the two halves of klode disagree about what a word is.
    3. **Casefold**, then split on any run of non-alphanumerics, judged by Unicode-aware
       `str.isalnum()` — an ASCII-only character class silently discarded every CJK,
       Cyrillic, and Greek word, making unrelated documents look identical.

    Punctuation and layout whitespace carry no evidence about extraction fidelity and vary freely
    between backends, so they are removed rather than compared.
    """
    t = unicodedata.normalize("NFKC", text)
    t = _DEHYPHEN.sub("", t)
    out, cur = [], []
    for ch in t.casefold():
        # `str.isalnum()` is Unicode-aware. An ASCII-only class ([^0-9a-z]) discarded EVERY
        # non-Latin character, so two entirely different CJK or Cyrillic documents tokenized to
        # the same ASCII residue and scored containment/inflation/order = 1.0 — a confident
        # `verified` on unrelated text.
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


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

    def __post_init__(self):
        # A NaN measurement makes every threshold comparison False, so the gate reads "nothing
        # exceeded a limit" and returns `verified` on numbers that mean nothing. Reject it where it
        # originates rather than letting it travel into a verdict and a provenance row.
        import math
        for name in ("containment", "inflation"):
            v = getattr(self, name)
            if not math.isfinite(v):
                raise ValueError(f"{name} must be finite, got {v!r}")
        for w in self.windows:
            if w.order is not None and not math.isfinite(w.order):
                raise ValueError(f"window {w.index} order must be finite or None, got {w.order!r}")

    @property
    def measured(self) -> tuple[float, ...]:
        return tuple(w.order for w in self.windows if w.order is not None)

    @property
    def abstained_windows(self) -> int:
        return sum(1 for w in self.windows if w.abstained)

    @property
    def measured_share(self) -> float:
        """Fraction of windows whose order could actually be measured. A verdict drawn from a
        minority of the document is a verdict about the minority."""
        return (len(self.measured) / len(self.windows)) if self.windows else 0.0

    @property
    def median(self) -> float | None:
        """The TRUE median of the measured window orders — the mean of the two middle values when
        the count is even. `quantile(0.5)` is a nearest-rank quantile, which for an even count
        returns one of the two middle observations; calling that "the median" reported 1.0 for
        `[0.0, 1.0]`."""
        vals = sorted(self.measured)
        if not vals:
            return None
        mid = len(vals) // 2
        return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2

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
            min_anchors: int = MIN_ANCHORS,
            control_pages: "list[str] | None" = None) -> Agreement:
    """Compare a candidate extraction against a control.

    `control_pages` — the control's text split by real page boundaries. When supplied, each window
    IS a page and the per-page guarantee is exact. When omitted, windows are fixed-size proxies and
    a document with short pages can hide per-page reversal inside a straddling window.
    """
    if window < 2:
        raise ValueError(f"window must be >= 2 tokens, got {window}")
    if control_pages is not None:
        a = []
        bounds = []
        for page in control_pages:
            toks = tokenize(page)
            bounds.append((len(a), len(a) + len(toks)))
            a.extend(toks)
        control = None                                    # `a` is authoritative now
    else:
        a = tokenize(control)
        bounds = [(i, min(i + window, len(a))) for i in range(0, len(a), window)] or [(0, 0)]
    b = tokenize(candidate)
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
    for wi, (start, stop) in enumerate(bounds):
        chunk = [(i, t) for i, t in enumerate(a[start:stop], start=start) if t in anchor_set]
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
