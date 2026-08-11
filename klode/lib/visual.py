"""Visual ground truth — the only signal here that is evidence of fidelity rather than of agreement.

Everything in `agreement.py` compares two extractors. That can establish *"these differ"*; it can
never establish *"the candidate is damaged"*, because the control (`pdftotext`) is the baseline
precisely when escalation has already judged its text layer unreliable. A correct OCR
reconstruction of a broken text layer disagrees with the control loudly and is still the better
text.

This module breaks that circularity the only way available without a human: **render the page and
read what is actually on it.** `pdftoppm` rasterises what a reader would see, `tesseract` reads the
raster, and the result is compared against the candidate's text for that page. The rendered page is
downstream of nothing — it is what the PDF *is*.

It is sampled, not exhaustive, because rendering and OCR-ing a 300-page book to verify one ingest
is not a cost anyone will pay. Sampling makes this a **statistical** claim, so the seed and the
selected page numbers are recorded: an unreproducible spot check is an anecdote.

OCR is itself lossy, so a low score is a *review prompt*, not a verdict — the same posture
`klode check --entail` already takes. Both binaries are optional; absent, this abstains loudly
rather than silently reporting success.
"""
from __future__ import annotations

import random
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from collections import Counter

from .agreement import tokenize

__all__ = ["available", "PageCheck", "VisualReport", "sample_pages", "check_pages"]

RENDER_DPI = 150            # enough for tesseract on body text; higher costs time for little gain
RENDER_TIMEOUT = 120
OCR_TIMEOUT = 120


def available() -> tuple[bool, str]:
    """(usable, why-not). Both binaries are needed; report which is missing rather than a bare False."""
    missing = [t for t in ("pdftoppm", "tesseract") if not shutil.which(t)]
    if missing:
        return False, f"missing {' and '.join(missing)}"
    return True, ""


@dataclass(frozen=True)
class PageCheck:
    page: int
    ocr_tokens: int
    matched: int                 # OCR tokens the candidate also has, counted as a MULTISET
    error: str = ""
    order: float | None = None   # rank agreement of the shared anchors on this page

    @property
    def recall(self) -> float | None:
        """Share of what the RENDERED page says that the candidate also contains.

        Deliberately recall, not F1: the failure being hunted is the candidate *missing* what the
        page shows. Extra candidate text (running heads, hyphenation artefacts) is not evidence of
        damage and must not be penalised here."""
        if self.error or not self.ocr_tokens:
            return None
        return self.matched / self.ocr_tokens


@dataclass(frozen=True)
class VisualReport:
    seed: int
    sampled: tuple[int, ...]
    checks: tuple[PageCheck, ...]
    skipped: str = ""            # non-empty when the check could not run at all

    @property
    def ran(self) -> bool:
        """The check executed. NOT the same as "produced a score" — see `measured`."""
        return not self.skipped

    @property
    def measured(self) -> bool:
        """At least one page yielded a recall. `ran` can be true while every page errored."""
        return bool(self.recalls)

    @property
    def worst_order(self) -> float | None:
        vals = [c.order for c in self.checks if c.order is not None]
        return min(vals) if vals else None

    @property
    def recalls(self) -> tuple[float, ...]:
        return tuple(c.recall for c in self.checks if c.recall is not None)

    @property
    def worst(self) -> PageCheck | None:
        scored = [c for c in self.checks if c.recall is not None]
        return min(scored, key=lambda c: c.recall) if scored else None

    def quantile(self, q: float) -> float | None:
        vals = sorted(self.recalls)
        if not vals:
            return None
        return vals[max(0, min(len(vals) - 1, int(round(q * (len(vals) - 1)))))]


def sample_pages(declared: int, n: int, seed: int) -> tuple[int, ...]:
    """Deterministic page sample. Same (declared, n, seed) always yields the same pages, so a
    reported result can be re-run by anyone."""
    if declared <= 0 or n <= 0:
        return ()
    pages = list(range(1, declared + 1))
    if n >= declared:
        return tuple(pages)
    return tuple(sorted(random.Random(seed).sample(pages, n)))


def _ocr_page(pdf: Path, page: int, workdir: Path) -> str:
    stem = workdir / f"p{page}"
    subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-r", str(RENDER_DPI),
                    "-png", "-singlefile", str(pdf), str(stem)],
                   check=True, capture_output=True, timeout=RENDER_TIMEOUT)
    png = stem.with_suffix(".png")
    if not png.is_file():
        raise RuntimeError(f"pdftoppm produced no image for page {page}")
    out = subprocess.run(["tesseract", str(png), "stdout"],
                         capture_output=True, text=True, timeout=OCR_TIMEOUT)
    if out.returncode != 0:
        raise RuntimeError(f"tesseract failed on page {page}: {out.stderr.strip()[:200]}")
    return out.stdout


def check_pages(pdf: Path, candidate_page_text: dict[int, str], *, pages: tuple[int, ...],
                seed: int) -> VisualReport:
    """OCR each sampled page and measure how much of it the candidate contains.

    `candidate_page_text` maps 1-indexed page -> the candidate's text for that page. A page the
    candidate cannot supply is recorded as an error, never scored as 0 — "no page-level text" and
    "wrong page-level text" are different findings.
    """
    ok, why = available()
    if not ok:
        return VisualReport(seed=seed, sampled=pages, checks=(), skipped=why)
    if not pdf.is_file():
        return VisualReport(seed=seed, sampled=pages, checks=(), skipped=f"no such pdf: {pdf}")

    checks: list[PageCheck] = []
    try:
        td_ctx = tempfile.TemporaryDirectory(prefix="klode-visual-")
    except OSError as e:      # no writable temp dir — a recorded skip, not an escaping exception
        return VisualReport(seed=seed, sampled=pages, checks=(),
                            skipped=f"cannot create a temporary directory ({e})")
    with td_ctx as td:
        work = Path(td)
        for page in pages:
            try:
                ocr = _ocr_page(pdf, page, work)
            except (subprocess.SubprocessError, RuntimeError, OSError) as e:
                checks.append(PageCheck(page, 0, 0, error=str(e)[:200]))
                continue
            ocr_toks = tokenize(ocr)
            if page not in candidate_page_text:
                checks.append(PageCheck(page, len(ocr_toks), 0,
                                        error="candidate has no text for this page"))
                continue
            cand_toks = tokenize(candidate_page_text[page])
            # MULTISET, not a set: with a set, a candidate containing one "the" matched all 100
            # OCR occurrences, so a page reduced to its vocabulary scored perfect recall.
            oc, cc = Counter(ocr_toks), Counter(cand_toks)
            matched = sum(min(n, cc[t]) for t, n in oc.items())
            # Recall alone is order-blind, which would let the very scrambling this module exists
            # to detect score 1.0. Rank-agree the anchors shared by OCR and candidate ON THIS PAGE.
            anchors = {t for t, n in oc.items() if n == 1 and cc.get(t) == 1}
            order = None
            if len(anchors) >= 4:
                seq = [t for t in ocr_toks if t in anchors]
                pos = {t: i for i, t in enumerate(cand_toks) if t in anchors}
                xs = list(range(len(seq)))
                rank = sorted(range(len(seq)), key=lambda k: pos[seq[k]])
                ys = [0] * len(seq)
                for r, k in enumerate(rank):
                    ys[k] = r
                n = len(xs)
                mx, my = sum(xs) / n, sum(ys) / n
                num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
                den = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
                order = (num / den) if den else None
            checks.append(PageCheck(page, len(ocr_toks), matched, order=order))
    return VisualReport(seed=seed, sampled=pages, checks=tuple(checks))
