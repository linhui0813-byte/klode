"""Tiered PDF → clean-source ingestion — the cheapest tool that reaches quality.

Escalate only when the cheap path fails, and decide by *measured* corruption of the
extraction, never by guesswork:

    Tier 1  pdftotext -layout   (poppler · free)        — PDFs with a usable text layer
    Tier 2  kreuzberg / xberg   (Rust + tesseract OCR)  — scanned prose, bad/no text layer
    Tier 3  docling             (layout models)         — complex multi-column/table docs

`auto` extracts with pdftotext, scores it, and only re-OCRs when the score says the text
layer is garbage — the exact genette (free) vs gerrig (needs OCR) split this pipeline was
built from. Every ingest records provenance (source sha256 + tier + tool version + score)
to `<lib>/PROVENANCE.jsonl`, so an ingested source is reproducible and its origin auditable.

lodlib core stays dependency-free: poppler is a system binary called over subprocess, and
the OCR tiers are lazy-imported — absent until `pipx inject lodlib kreuzberg` (or docling).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .normalize import strip_page_furniture

CLEAN_THRESHOLD = 5.0          # corruption/10k below which the text layer is trusted (empirical)
MIN_WORDS = 200                # guard: an "empty but clean" extraction is not a win
_TILDE = re.compile(r"[A-Za-z]+~[A-Za-z]+")
_MISCAP = re.compile(r"\b[a-z]{2,}[A-Z]{2}[a-z]*\b")


def corruption_score(text: str) -> float:
    """OCR-corruption markers per 10k words — tilde-in-word (`t~e`) + mid-word caps
    (`mfonnafion`/`regulatIOn`). The signal that split clean sources from garbled ones."""
    w = len(text.split()) or 1
    return (len(_TILDE.findall(text)) + len(_MISCAP.findall(text))) / w * 10000


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---- the three extractors -------------------------------------------------
def _pdftotext(pdf: Path, lang: str = "eng") -> str:
    if not shutil.which("pdftotext"):
        raise RuntimeError("pdftotext not found — install poppler (brew install poppler)")
    out = subprocess.run(["pdftotext", "-layout", str(pdf), "-"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"pdftotext failed: {out.stderr.strip()[:200]}")
    return out.stdout


def _xberg(pdf: Path, lang: str = "eng") -> str:
    try:
        from kreuzberg import extract_file_sync, ExtractionConfig
    except ImportError as e:
        raise ImportError("xberg/kreuzberg not installed — `pipx inject lodlib kreuzberg` "
                          "(needs the tesseract binary too)") from e
    r = extract_file_sync(str(pdf), config=ExtractionConfig(force_ocr=True))
    return r.content or ""


def _docling(pdf: Path, lang: str = "eng") -> str:
    try:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions
        from docling.datamodel.base_models import InputFormat
    except ImportError as e:
        raise ImportError("docling not installed — `pipx inject lodlib docling` "
                          "(heavy: torch + models). Reserve for complex-layout docs.") from e
    opts = PdfPipelineOptions()
    opts.do_ocr = True
    try:
        opts.force_full_page_ocr = True
        opts.ocr_options = TesseractCliOcrOptions()      # match xberg's engine; skip the Chinese default
    except Exception:
        pass
    conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    return conv.convert(str(pdf)).document.export_to_markdown()


_EXTRACTORS = {"pdftotext": _pdftotext, "xberg": _xberg, "docling": _docling}


@dataclass
class Choice:
    tier: str
    text: str
    score: float
    note: str = ""


def choose_and_extract(pdf: Path, tier: str = "auto", lang: str = "eng") -> Choice:
    """Pick a tier. Forced tiers run as asked; `auto` uses the cheap path unless its
    measured corruption says otherwise, then takes the better of pdftotext / OCR."""
    if tier != "auto":
        text = _EXTRACTORS[tier](pdf, lang)
        return Choice(tier, text, corruption_score(text))

    t1 = _pdftotext(pdf, lang)
    s1 = corruption_score(t1)
    if s1 < CLEAN_THRESHOLD and len(t1.split()) >= MIN_WORDS:
        return Choice("pdftotext", t1, s1, "text layer clean")
    # the cheap layer is garbage (or empty) — re-OCR
    try:
        t2 = _xberg(pdf, lang)
        s2 = corruption_score(t2)
        if s2 <= s1:
            return Choice("xberg", t2, s2, f"escalated: pdftotext scored {s1:.1f}")
        return Choice("pdftotext", t1, s1, f"xberg ({s2:.1f}) no better than pdftotext")
    except ImportError as e:
        return Choice("pdftotext", t1, s1, f"WANTED OCR but {e}")


# ---- the ingest flow ------------------------------------------------------
@dataclass
class IngestResult:
    id: str
    shelf: str
    tier: str
    score_before: float
    score_after: float
    words: int
    furniture_stripped: int
    dest: str
    provenance: str
    note: str


def _tool_version(tier: str) -> str:
    try:
        if tier == "pdftotext":
            v = subprocess.run(["pdftotext", "-v"], capture_output=True, text=True)
            return (v.stderr or v.stdout).splitlines()[0].strip()
        if tier == "xberg":
            import kreuzberg
            return f"kreuzberg {getattr(kreuzberg, '__version__', '?')}"
        if tier == "docling":
            import docling
            return f"docling {getattr(docling, '__version__', '?')}"
    except Exception:
        pass
    return tier


def _slug(name: str) -> str:
    stem = Path(name).stem.lower()
    stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    return stem or "source"


def ingest(cfg: Config, pdf: Path, shelf: str, *, card_id: str | None = None,
           tier: str = "auto", lang: str = "eng", force: bool = False) -> IngestResult:
    if shelf not in cfg.shelves:
        raise ValueError(f"unknown shelf {shelf!r}; configured: {', '.join(cfg.shelves)}")
    if not pdf.is_file():
        raise FileNotFoundError(pdf)
    if card_id is not None:
        # a user-supplied --id must be a bare filename stem; a `/`/`..` id would escape the shelf
        if not re.fullmatch(r"[A-Za-z0-9._-]+", card_id) or card_id in (".", ".."):
            raise ValueError(f"invalid --id {card_id!r}: use letters, digits, '.', '_', '-' only "
                             "(no path separators)")
        cid = card_id
    else:
        cid = _slug(pdf.name)
    dest = cfg.lib / shelf / f"{cid}.txt"
    if not str(dest.resolve()).startswith(str(cfg.lib.resolve()) + os.sep):
        raise ValueError(f"refusing to write outside the library tree: {dest}")
    if dest.exists() and not force:
        raise FileExistsError(f"{dest} exists (use force to overwrite)")

    choice = choose_and_extract(pdf, tier, lang)
    cleaned, stripped, _ex = strip_page_furniture(choice.text)
    score_after = corruption_score(cleaned)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(cleaned, encoding="utf-8")

    prov = {
        "id": cid, "shelf": shelf,
        "source_pdf": pdf.name, "source_sha256": sha256(pdf),
        "tier": choice.tier, "tool": _tool_version(choice.tier),
        "words": len(cleaned.split()),
        "corruption_before": round(choice.score, 2),
        "corruption_after": round(score_after, 2),
        "furniture_stripped": stripped,
        "ingested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    prov_path = cfg.lib / "PROVENANCE.jsonl"
    with open(prov_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(prov, ensure_ascii=False) + "\n")

    return IngestResult(
        id=cid, shelf=shelf, tier=choice.tier,
        score_before=round(choice.score, 2), score_after=round(score_after, 2),
        words=len(cleaned.split()), furniture_stripped=stripped,
        dest=os.path.relpath(dest, cfg.root),
        provenance=os.path.relpath(prov_path, cfg.root), note=choice.note)


def run(cfg: Config, args) -> int:
    pdf = Path(args.pdf).expanduser()
    try:
        r = ingest(cfg, pdf, args.shelf, card_id=args.id, tier=args.tier,
                   lang=args.lang, force=args.force)
    except Exception as e:      # incl. an installed-but-failing OCR backend — report, never crash
        print(f"ingest error: {e}", file=sys.stderr)
        return 1
    print(f"ingested {pdf.name}")
    print(f"  tier    : {r.tier}  ({r.note})")
    print(f"  corruption: {r.score_before} -> {r.score_after} /10k  ({r.words} words)")
    print(f"  furniture : {r.furniture_stripped} lines stripped")
    print(f"  source  : {r.dest}")
    print(f"  provenance: {r.provenance}")
    print(f"\nnext: add a BIBLIOGRAPHY row, `lib build`, write the grep-anchored Thin/Full, `lib check`")
    return 0
