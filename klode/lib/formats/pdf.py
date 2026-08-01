"""PDF handler — the tiered extractor, moved here unchanged in behaviour from ingest.py.

    Tier 1  pdftotext -layout   (poppler · free)        — PDFs with a usable text layer
    Tier 2  kreuzberg / xberg   (Rust + tesseract OCR)  — scanned prose, bad/no text layer
    Tier 3  docling             (layout models)         — complex multi-column/table docs

`auto` extracts with pdftotext, scores it, and re-OCRs only when the score says the text layer
is garbage — escalating pdftotext -> xberg -> docling (Tier 3 is now reachable from `auto`).
Poppler is a system binary called over subprocess; the OCR tiers are lazy-imported, so importing
this module pulls in no backend."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

from ._base import Extraction

CLEAN_THRESHOLD = 5.0          # corruption/10k below which the text layer is trusted (empirical)
MIN_WORDS = 200                # guard: an "empty but clean" extraction is not a win
DOCLING_ENV = "KLODE_DOCLING_URL"    # a docling-serve endpoint, e.g. http://<host>:15001 — keep it
DOCLING_HTTP_TIMEOUT = 300          # on a trusted/private network + uncommitted. Env, not config.
MAX_DOCLING_RESPONSE = 64 * 1024 * 1024   # cap the server response bytes read into memory (OOM guard)
_TILDE = re.compile(r"[A-Za-z]+~[A-Za-z]+")
_MISCAP = re.compile(r"\b[a-z]{2,}[A-Z]{2}[a-z]*\b")


def corruption_score(text: str) -> float:
    """OCR-corruption markers per 10k words — tilde-in-word (`t~e`) + mid-word caps
    (`mfonnafion`/`regulatIOn`). The signal that split clean sources from garbled ones."""
    w = len(text.split()) or 1
    return (len(_TILDE.findall(text)) + len(_MISCAP.findall(text))) / w * 10000


PDFTOTEXT_TIMEOUT = 120        # seconds — a hostile/wedged PDF must not hang ingestion forever


def _pdftotext(pdf: Path, lang: str = "eng") -> str:
    if not shutil.which("pdftotext"):
        raise RuntimeError("pdftotext not found — install poppler (brew install poppler)")
    try:
        out = subprocess.run(["pdftotext", "-layout", str(pdf), "-"],
                             capture_output=True, text=True, timeout=PDFTOTEXT_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"pdftotext timed out (>{PDFTOTEXT_TIMEOUT}s) — possibly a hostile PDF")
    if out.returncode != 0:
        raise RuntimeError(f"pdftotext failed: {out.stderr.strip()[:200]}")
    return out.stdout


def _xberg(pdf: Path, lang: str = "eng") -> str:
    try:
        from kreuzberg import extract_file_sync, ExtractionConfig
    except ImportError as e:
        raise ImportError("xberg/kreuzberg not installed — `pipx inject klode kreuzberg` "
                          "(needs the tesseract binary too)") from e
    r = extract_file_sync(str(pdf), config=ExtractionConfig(force_ocr=True))
    return r.content or ""


def _multipart(fields: dict, file_field: str, filename: str, file_bytes: bytes,
               boundary: str) -> bytes:
    """Build a multipart/form-data body with stdlib only (no `requests` dependency). The filename
    is stripped of quotes/CR/LF so it cannot inject extra multipart headers."""
    safe_name = filename.replace('"', "").replace("\r", "").replace("\n", "")
    parts: list[bytes] = []
    for k, v in fields.items():
        parts += [f"--{boundary}".encode(),
                  f'Content-Disposition: form-data; name="{k}"'.encode(), b"", str(v).encode()]
    parts += [f"--{boundary}".encode(),
              f'Content-Disposition: form-data; name="{file_field}"; filename="{safe_name}"'.encode(),
              b"Content-Type: application/pdf", b"", file_bytes,
              f"--{boundary}--".encode(), b""]
    return b"\r\n".join(parts)


def _docling_remote(pdf: Path, endpoint: str) -> str:
    """Convert via a docling-serve endpoint (the GPU lives on the server; klode stays zero-dep).
    Returns the document's markdown. Network/HTTP failure raises OSError and a malformed/oversized
    response raises RuntimeError, so the escalation loop degrades to the best local tier instead of
    crashing."""
    boundary = uuid.uuid4().hex
    body = _multipart({"to_formats": "md", "do_table_structure": "true"},
                      "files", pdf.name, pdf.read_bytes(), boundary)
    req = urllib.request.Request(
        f"{endpoint}/v1/convert/file", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=DOCLING_HTTP_TIMEOUT) as resp:
        raw = resp.read(MAX_DOCLING_RESPONSE + 1)         # bounded read: never OOM on a huge response
    if len(raw) > MAX_DOCLING_RESPONSE:
        raise RuntimeError("docling-serve response exceeds the size cap")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:         # JSONDecodeError is a ValueError
        raise RuntimeError(f"docling-serve returned an unparseable response ({e})") from e
    md = (data.get("document") or {}).get("md_content") or ""
    if not md.strip():
        raise RuntimeError("docling-serve returned no markdown")
    return md


def _docling(pdf: Path, lang: str = "eng") -> str:
    endpoint = os.environ.get(DOCLING_ENV)
    if endpoint:                                          # remote docling-serve (GPU lives server-side)
        endpoint = endpoint.rstrip("/")
        if not endpoint.startswith(("http://", "https://")):
            raise RuntimeError(f"{DOCLING_ENV} must be an http(s) URL, got {endpoint!r}")
        return _docling_remote(pdf, endpoint)
    try:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions
        from docling.datamodel.base_models import InputFormat
    except ImportError as e:
        raise ImportError("docling not installed — set $KLODE_DOCLING_URL to a docling-serve "
                          "endpoint, or `pipx inject klode docling` (heavy: torch + models).") from e
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
    """Pick a tier. Forced tiers run as asked; `auto` uses the cheap path unless its measured
    corruption says otherwise, then escalates pdftotext -> xberg -> docling, taking the best."""
    if tier != "auto":
        fn = _EXTRACTORS.get(tier)
        if fn is None:
            raise ValueError(f"unknown tier {tier!r}; choose one of {', '.join(_EXTRACTORS)}")
        text = fn(pdf, lang)
        return Choice(tier, text, corruption_score(text))

    def _better(cur: Choice, name: str, text: str, note: str) -> Choice:
        # never replace usable text with an EMPTY/short OCR result (an empty result scores 0 and
        # would otherwise look "clean"); otherwise prefer strictly-lower corruption.
        if not text.split():
            return cur
        score = corruption_score(text)
        if not cur.text.split() or score <= cur.score:
            return Choice(name, text, score, note)
        return cur

    # Tier 1 — pdftotext (cheap). A missing/failing Poppler is an escalation reason, not an abort.
    try:
        t1 = _pdftotext(pdf, lang)
        s1 = corruption_score(t1)
        if s1 < CLEAN_THRESHOLD and len(t1.split()) >= MIN_WORDS:
            return Choice("pdftotext", t1, s1, "text layer clean")
        best = Choice("pdftotext", t1, s1, f"pdftotext scored {s1:.1f}")
    except (RuntimeError, OSError) as e:
        best = Choice("pdftotext", "", float("inf"), f"pdftotext failed ({e})")

    try:                                                  # Tier 2 — OCR
        best = _better(best, "xberg", _xberg(pdf, lang), f"escalated: pdftotext scored {best.score:.1f}")
    except ImportError as e:
        best = Choice(best.tier, best.text, best.score, f"WANTED OCR but {e}")
    except (RuntimeError, OSError) as e:                  # backend runtime failure, not a bug
        best = Choice(best.tier, best.text, best.score, f"{best.note}; xberg failed ({e})")

    if not best.text.split() or best.score >= CLEAN_THRESHOLD:   # still unusable — Tier 3
        try:
            best = _better(best, "docling", _docling(pdf, lang),
                           f"escalated to docling: prior scored {best.score:.1f}")
        except (ImportError, RuntimeError, OSError) as e:
            best = Choice(best.tier, best.text, best.score, f"{best.note}; docling absent ({e})")
    return best


class PdfHandler:
    name = "pdf"
    format = "pdf"
    priority = 5

    def sniff(self, path: Path, head: bytes) -> bool:
        return head.startswith(b"%PDF")

    def extract(self, path: Path, *, lang: str = "eng", tier: str = "auto") -> Extraction:
        c = choose_and_extract(path, tier, lang)
        return Extraction(text=c.text, handler=c.tier, format="pdf", note=c.note)
