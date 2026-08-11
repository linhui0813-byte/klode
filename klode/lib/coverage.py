"""Page coverage — did the extraction actually represent every page of the PDF?

The distinction this module exists to preserve: **control coverage is not candidate coverage.** An
earlier design counted `pdfinfo` pages against the control's form-feed splits and called that "did
we get every page?". It is not — that number is byte-identical whether the candidate kept every
page or dropped half of them, because the candidate never appears in it.

So coverage is answered from whichever side can actually answer:

- **Declared** — `pdfinfo`'s `Pages:`. The only authority on how many pages exist.
- **Control** — `pdftotext` emits `\\f` between pages, so per-page word counts are directly
  available. This is what finds a genuinely blank page.
- **Candidate** — a layout backend's *structured* result carries `prov[].page_no` per block. When
  present, candidate coverage is **direct**. When absent, it is `None` — not inferred, not assumed.

`None` is the honest answer for a markdown-only backend, and callers must treat it as "unknown",
never as "fine". Stdlib only; `pdfinfo` is invoked by the caller, not here.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["split_pages", "pages_from_docling", "Coverage", "assess"]


def split_pages(text: str) -> list[str]:
    """Per-page text from a form-feed-separated extraction.

    `pdftotext` emits a trailing `\\f` after the LAST page, so `"A\\fB\\fC\\f".split("\\f")` yields
    four elements for three pages. Exactly one trailing empty segment is dropped — no more, because
    a genuinely blank final page must still be counted as a page.
    """
    parts = text.split("\f")
    if len(parts) > 1 and parts[-1] == "":
        parts = parts[:-1]
    return parts


def pages_from_docling(doc) -> tuple[int, ...] | None:
    """Page numbers a docling structured result claims to represent, or None when it cannot say.

    Reads both shapes docling emits: the `pages` map keyed by page number, and `prov[].page_no` on
    each text/table/picture block. Returns None — meaning *unknown* — rather than an empty tuple
    when the document carries no provenance at all, because "no pages" and "cannot tell" are
    different answers and only one of them is alarming.
    """
    if not isinstance(doc, dict):
        return None
    found: set[int] = set()

    # NOTE: the top-level `pages` map is deliberately NOT read. It is docling's inventory of the
    # SOURCE's pages, not evidence that content was extracted from each one — a document declaring
    # pages 1-3 whose blocks carry provenance for only 1 and 3 would otherwise report full
    # coverage and mask the missing page. Only content provenance counts as coverage.
    for key in ("texts", "tables", "pictures", "body"):
        blocks = doc.get(key)
        if not isinstance(blocks, list):
            continue
        for b in blocks:
            if not isinstance(b, dict):
                continue
            prov = b.get("prov")
            if not isinstance(prov, list):     # `prov: 1` raised TypeError; a str silently iterated
                continue
            for pr in prov:
                if isinstance(pr, dict):
                    n = pr.get("page_no")
                    if isinstance(n, int) and not isinstance(n, bool):
                        found.add(n)
    return tuple(sorted(found)) or None


@dataclass(frozen=True)
class Coverage:
    declared: int                        # pdfinfo Pages:, or 0 when unavailable
    control_pages: int                   # form-feed segments in the control extraction
    control_empty: tuple[int, ...]       # 1-indexed control pages yielding no words
    candidate_pages: tuple[int, ...] | None   # None = the backend cannot say
    min_words: int

    @property
    def candidate_known(self) -> bool:
        return self.candidate_pages is not None

    @property
    def candidate_missing(self) -> tuple[int, ...]:
        """Declared pages the candidate does not claim. Empty when the candidate cannot say —
        callers must check `candidate_known` first rather than reading emptiness as success."""
        if self.candidate_pages is None or not self.declared:
            return ()
        have = set(self.candidate_pages)      # build once, not per declared page
        return tuple(p for p in range(1, self.declared + 1) if p not in have)

    @property
    def control_mismatch(self) -> bool:
        """The control disagrees with pdfinfo about how many pages exist — a signal about the
        CONTROL, not the candidate."""
        return bool(self.declared) and self.control_pages != self.declared


def assess(declared: int, control_text: str, candidate_pages: tuple[int, ...] | None = None, *,
           min_words: int = 1) -> Coverage:
    """Combine the three views. `declared=0` means `pdfinfo` was unavailable — reported, never
    silently treated as full coverage."""
    pages = split_pages(control_text) if control_text else []
    empty = tuple(i for i, p in enumerate(pages, start=1) if len(p.split()) < min_words)
    return Coverage(declared=declared, control_pages=len(pages), control_empty=empty,
                    candidate_pages=candidate_pages, min_words=min_words)
