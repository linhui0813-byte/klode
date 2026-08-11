#!/usr/bin/env python3
"""Generate the labeled PDF corpus (WI-0) — stdlib only, deterministic, committable.

Why authored rather than collected: page-level ground truth has to come from something other than
an extractor, or the corpus begs the question the plan exists to settle. Text placed at known
coordinates on a known page IS the ground truth — no transcription, no licensing, and the whole
corpus regenerates byte-identically from this file.

    python3 tests/fixtures/pdfs/make_fixtures.py       # rewrites *.pdf + GROUND-TRUTH.json

What this corpus DOES cover: page counts, blank pages, two-column reading order, running heads,
repeated content. What it does NOT cover, and what still needs real files: scanned rasters, a
genuinely broken text layer (visible render disagreeing with the text layer), real-world layout
complexity, non-Latin scripts. Those gaps are recorded in GROUND-TRUTH.json under `not_covered`, so
a later reader cannot mistake this for a complete evaluation set.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAGE_W, PAGE_H = 612, 792


def _esc(s: str) -> str:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _content(blocks: list[tuple[int, int, list[str]]], size: int = 12, leading: int = 16) -> bytes:
    """A content stream from (x, y, lines) blocks. Each block is an independent BT/ET run, which is
    how a real two-column layout is emitted — and why reading order is a genuine question."""
    out = []
    for x, y, lines in blocks:
        out.append(f"BT /F1 {size} Tf {leading} TL {x} {y} Td")
        for i, line in enumerate(lines):
            out.append(f"({_esc(line)}) Tj" if i == 0 else f"T* ({_esc(line)}) Tj")
        out.append("ET")
    return "\n".join(out).encode("latin-1")


def write_pdf(path: Path, pages: list[bytes]) -> None:
    """A minimal, valid, uncompressed PDF with a correct xref table."""
    objs: list[bytes] = []

    def add(b: bytes) -> int:
        objs.append(b)
        return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pages_id = len(objs) + 1 + 2 * len(pages)      # reserve: page objs + content objs come first
    kids, page_ids = [], []
    for content in pages:
        cid = add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content))
        pid = add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R "
            b"/Resources << /Font << /F1 %d 0 R >> >> >>" % (pages_id, PAGE_W, PAGE_H, cid, font))
        page_ids.append(pid)
        kids.append(b"%d 0 R" % pid)
    real_pages = add(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(kids), len(pages)))
    assert real_pages == pages_id, f"page-tree id drifted: {real_pages} != {pages_id}"
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

    buf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objs, start=1):
        offsets.append(len(buf))
        buf += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref_at = len(buf)
    buf += b"xref\n0 %d\n" % (len(objs) + 1)
    buf += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        buf += b"%010d 00000 n \n" % off
    buf += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1, catalog, xref_at)
    path.write_bytes(bytes(buf))


# --------------------------------------------------------------------------- the corpus

LOREM = ("The best modules are those whose interfaces are much simpler than their implementations "
         "Such modules have two advantages First a simple interface minimizes the complexity that "
         "a module imposes on the rest of the system").split()


def _para(seed: int, n: int) -> list[str]:
    """Deterministic pseudo-prose: distinct per page, with enough unique tokens to anchor on."""
    return [" ".join(LOREM[(seed + i + j) % len(LOREM)] for j in range(7)) + f" p{seed}w{i}"
            for i in range(n)]


def build() -> dict:
    truth: dict = {"generator": "make_fixtures.py", "page_size": [PAGE_W, PAGE_H], "files": {}}

    # 1. three plain pages — the baseline for page counting
    pages, gt = [], []
    for p in range(1, 4):
        lines = _para(p * 10, 8)
        pages.append(_content([(72, 700, lines)]))
        gt.append(lines)
    write_pdf(HERE / "three-pages.pdf", pages)
    truth["files"]["three-pages.pdf"] = {
        "pages": 3, "why": "baseline page counting and per-page text", "text": gt}

    # 2. a blank middle page — coverage must notice a page that yields no words
    pages, gt = [], []
    for p in (1, 2, 3):
        if p == 2:
            pages.append(b"")                      # genuinely empty content stream
            gt.append([])
        else:
            lines = _para(p * 20, 8)
            pages.append(_content([(72, 700, lines)]))
            gt.append(lines)
    write_pdf(HERE / "blank-middle.pdf", pages)
    truth["files"]["blank-middle.pdf"] = {
        "pages": 3, "why": "page 2 is empty — coverage must report it, not average it away",
        "empty_pages": [2], "text": gt}

    # 3. two columns per page — the reading-order case the whole plan is about
    pages, gt = [], []
    for p in (1, 2):
        left = _para(p * 30, 10)
        right = _para(p * 30 + 5, 10)
        pages.append(_content([(60, 700, left), (330, 700, right)], size=10, leading=14))
        gt.append({"left": left, "right": right})
    write_pdf(HERE / "two-column.pdf", pages)
    truth["files"]["two-column.pdf"] = {
        "pages": 2, "why": "two independent text blocks per page; correct reading order is "
                           "left column fully, then right column",
        "columns": 2, "text": gt}

    # 4. a repeated running head — normalize() strips these, so verification bound to the
    #    PRE-normalization text would disagree with what is actually persisted
    pages, gt = [], []
    for p in range(1, 4):
        body = _para(p * 40, 6)
        pages.append(_content([(72, 740, ["A PHILOSOPHY OF SOFTWARE DESIGN"]), (72, 700, body)]))
        gt.append(body)
    write_pdf(HERE / "running-head.pdf", pages)
    truth["files"]["running-head.pdf"] = {
        "pages": 3, "why": "identical head on every page — exercises furniture stripping",
        "running_head": "A PHILOSOPHY OF SOFTWARE DESIGN", "text": gt}

    # 5. one page — off-by-one guard for the form-feed page split
    write_pdf(HERE / "single-page.pdf", [_content([(72, 700, _para(90, 5))])])
    truth["files"]["single-page.pdf"] = {
        "pages": 1, "why": "trailing form-feed off-by-one guard", "text": [_para(90, 5)]}

    truth["not_covered"] = [
        "scanned raster pages (needs a real scan)",
        "a broken text layer whose invisible text disagrees with the render",
        "non-Latin scripts and mixed-language documents",
        "real-world table and footnote layout complexity",
    ]
    return truth


if __name__ == "__main__":
    t = build()
    (HERE / "GROUND-TRUTH.json").write_text(json.dumps(t, indent=2) + "\n", encoding="utf-8")
    for name in sorted(t["files"]):
        print(f"  wrote {name} ({t['files'][name]['pages']} pages)")
    print(f"  wrote GROUND-TRUTH.json ({len(t['files'])} files, "
          f"{len(t['not_covered'])} documented gaps)")
