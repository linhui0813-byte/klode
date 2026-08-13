"""Ingest a source of any supported format into a clean, grep-ready `.txt` on a shelf.

The orchestrator: route the file to a format handler (PDF/EPUB/DOCX/HTML/TXT), extract raw
text, guard against an empty/garbage extraction, run the FULL normalize pipeline (ligatures,
control-byte repair, page-furniture, de-wrap, ascii-ligatures), write the source, and record
provenance. Extraction itself lives in `klode.lib.formats`; this module owns the pipeline around
it. Corruption scoring stays importable here for backward compatibility."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import agreement, coverage, formats, integrity
from .config import Config
from .formats._base import ExtractionError
from .formats.pdf import CLEAN_THRESHOLD, MIN_WORDS, corruption_score  # re-export: back-compat
from .normalize import CTRL, load_dict, process

__all__ = ["ingest", "run", "quality_signal", "corruption_score",
           "CLEAN_THRESHOLD", "MIN_WORDS", "sha256", "IngestResult"]

GARBAGE_CTRL_RATIO = 0.10      # >10% control chars => binary decoded as text; refuse before writing
CONTROL_TIER = "pdftotext"     # the deterministic tier used as the agreement control


class VerificationError(ExtractionError):
    """Extraction integrity was MEASURED and failed. Raised before any shelf write, so a failed
    verification never leaves a source behind — the same pre-write posture as the existing garbage
    guards. `--accept-unverified` records the failure and writes anyway."""


def _declared_pages(source: Path) -> int:
    """`pdfinfo` page count, or 0 when poppler is absent or the file is not a PDF. 0 means
    UNKNOWN and is reported as such — never silently treated as full coverage."""
    if not shutil.which("pdfinfo"):
        return 0
    try:
        out = subprocess.run(["pdfinfo", str(source)], capture_output=True, text=True, timeout=60)
    except (subprocess.SubprocessError, OSError):
        return 0
    for line in out.stdout.splitlines():
        if line.startswith("Pages:"):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return 0
    return 0


_PAGE_MARK = "" * 16
"""A page boundary that survives normalization. Chosen against every rule in `normalize.py`:
16 private-use characters are too long for the OCR-noise class (≤12), carry no letter (so
`running_head` never matches), are not control bytes (so `repair_ctrl` leaves them), and are not
ligature or dictionary material. A bare `\\f` does not survive — `repair_ctrl` strips it to a
space, which is why page boundaries have to be re-expressed before normalizing."""


def _normalize_control(control_raw: str, dic) -> "tuple[str, list[str] | None]":
    """Normalize the control ONCE, as a whole document, keeping page boundaries.

    Normalizing page-at-a-time is a DIFFERENT pipeline from the one the candidate went through.
    `strip_page_furniture` only recognizes a running head that repeats ≥5 times *across the text it
    is given*, so a head stripped from the whole-document candidate survives in a page-at-a-time
    control — six pages of "CHAPTER ONE" become six retained lines against zero. The control then
    holds tokens the candidate correctly dropped, and containment falls (and inflation with it) for
    a faithful extraction: a manufactured failure.

    Returns `(text, pages)`. `pages is None` means the boundaries could not be recovered and the
    caller must fall back to fixed-size windows — a weaker order guarantee (documented in
    `agreement.compare`), but never a wrong containment number.
    """
    pages = coverage.split_pages(control_raw)
    if len(pages) < 2 or _PAGE_MARK in control_raw:
        return process(control_raw, dic)[0], None
    cleaned = process(f"\n\n{_PAGE_MARK}\n\n".join(pages), dic)[0]
    if cleaned.count(_PAGE_MARK) != len(pages) - 1:
        # normalization consumed or duplicated a marker; report the text without page claims
        return process(control_raw, dic)[0], None
    out = cleaned.split(_PAGE_MARK)
    return "\n".join(out), out


def verify_extraction(cfg: Config, source: Path, ext, cleaned: str, dic) -> "integrity.Integrity":
    """Measure the candidate against a control and the page counts.

    Verification runs on the NORMALIZED text, because that is what gets persisted: normalization
    strips running heads and folds hyphenation, so a verdict about the pre-normalization text is a
    verdict about bytes nobody will ever read.

    Abstains (rather than failing) whenever it cannot measure: non-PDF, the control tier IS the
    candidate, or poppler absent. That is not the same as passing, and `Integrity.ok` says so.
    """
    if ext.format != "pdf" or ext.handler == CONTROL_TIER:
        return integrity.Integrity(integrity.ABSTAINED,
                                   ("control tier is the candidate, or not a PDF — nothing to "
                                    "compare against",), {}, integrity.Thresholds().as_dict())
    try:
        from .formats.pdf import _pdftotext
        control_raw = _pdftotext(source)
    except (RuntimeError, OSError) as e:
        return integrity.Integrity(integrity.ABSTAINED, (f"control extraction unavailable ({e})",),
                                   {}, integrity.Thresholds().as_dict())
    # Real page boundaries must survive into `compare` — without them the window is a fixed
    # 400-token proxy, and a book with short pages can have every page internally reversed while
    # each straddling window still scores ~0.98. They are re-expressed as a marker rather than
    # recovered by normalizing page-at-a-time, which would silently change the pipeline (see
    # `_normalize_control`).
    control_clean, control_pages = _normalize_control(control_raw, dic)
    agr = agreement.compare(control_clean, cleaned, control_pages=control_pages)
    cov = coverage.assess(_declared_pages(source), control_raw, ext.pages)
    return integrity.decide(agr, cov)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def quality_signal(text: str) -> dict:
    """Format-agnostic quality of an extraction. `corruption` is the OCR metric (PDF escalation
    still uses it); `words` and `control_char_ratio` drive the empty/garbage guard for every
    format. Pure and backend-free."""
    ctrl = sum(1 for ch in text if ord(ch) in CTRL)
    return {"words": len(text.split()),
            "corruption": corruption_score(text),
            "control_char_ratio": (ctrl / len(text)) if text else 0.0}


def _tool_version(handler: str) -> str:
    try:
        if handler == "pdftotext":
            v = subprocess.run(["pdftotext", "-v"], capture_output=True, text=True)
            return (v.stderr or v.stdout).splitlines()[0].strip()
        if handler == "xberg":
            import kreuzberg
            return f"kreuzberg {getattr(kreuzberg, '__version__', '?')}"
        if handler == "docling":
            import docling
            return f"docling {getattr(docling, '__version__', '?')}"
    except Exception:
        pass
    return f"stdlib:{handler}" if handler in ("txt", "html", "epub", "docx") else handler


def _slug(name: str) -> str:
    stem = Path(name).stem.lower()
    stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    return stem or "source"


@dataclass
class IngestResult:
    id: str
    shelf: str
    format: str
    handler: str
    score_before: float
    score_after: float
    words: int
    furniture_stripped: int
    dest: str
    provenance: str
    note: str
    verification: "integrity.Integrity | None" = None



def _provenance_row(cid, shelf, source, src_sha, ext, cleaned, stats, verdict, out_sha,
                    q_before=None, q_after=None) -> dict:
    """The provenance record. Built BEFORE promotion so an unwritable log cannot leave a shelf
    artifact behind, and bound to `out_sha` — the hash of the exact bytes about to be written."""
    tool = _tool_version(ext.handler)          # computed once; it can spawn a subprocess
    return {
        "id": cid, "shelf": shelf,
        "format": ext.format, "handler": ext.handler,
        "source": source.name, "source_sha256": src_sha,
        "tool": tool,
        "words": len(cleaned.split()),
        "corruption_before": round((q_before or {}).get("corruption", 0.0), 2),
        "corruption_after": round((q_after or {}).get("corruption", 0.0), 2),
        "furniture_stripped": stats.get("furniture", 0),
        "ingested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # bind the verification to the bytes actually written: changing one byte of the shelf
        # artifact orphans this record rather than inheriting its verdict.
        "output_sha256": out_sha,
        "verification": {
            "schema": integrity.SCHEMA,
            "status": verdict.state,
            "reasons": list(verdict.reasons),
            "metrics": verdict.metrics,
            "thresholds": verdict.thresholds,
            "control_tier": CONTROL_TIER,
            "candidate_tier": ext.handler,
            "tool": tool,
        },
    }


def _rollback_provenance(prov_path: Path, before: int, written: int) -> None:
    """Remove a just-appended row after the promotion it describes failed.

    Truncates ONLY when the log is still exactly `before + written` bytes — i.e. nothing else has
    appended since. A concurrent ingest's row must never be destroyed to tidy up this one's, so a
    surprising size means the row is left in place and the caller's exception still propagates.
    """
    try:
        if prov_path.stat().st_size == before + written:
            os.truncate(prov_path, before)
    except OSError:
        pass          # best effort: the failure being handled is the one worth reporting


def ingest(cfg: Config, source: Path, shelf: str, *, card_id: str | None = None,
           tier: str = "auto", lang: str = "eng", force: bool = False,
           fmt: str | None = None, verify: bool = True,
           accept_unverified: bool = False) -> IngestResult:
    if shelf not in cfg.shelves:
        raise ValueError(f"unknown shelf {shelf!r}; configured: {', '.join(cfg.shelves)}")
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    if card_id is not None:
        # a user-supplied --id must be a bare filename stem; a `/`/`..` id would escape the shelf
        if not re.fullmatch(r"[A-Za-z0-9._-]+", card_id) or card_id in (".", ".."):
            raise ValueError(f"invalid --id {card_id!r}: use letters, digits, '.', '_', '-' only "
                             "(no path separators)")
        cid = card_id
    else:
        cid = _slug(source.name)
    dest = cfg.lib / shelf / f"{cid}.txt"
    if not str(dest.resolve()).startswith(str(cfg.lib.resolve()) + os.sep):
        raise ValueError(f"refusing to write outside the library tree: {dest}")
    if dest.exists() and not force:
        raise FileExistsError(f"{dest} exists (use force to overwrite)")

    handler = formats.route(source, fmt=fmt)              # UnsupportedFormat if nothing matches
    ext = handler.extract(source, lang=lang, tier=tier)

    # Guard BEFORE any write: never persist a garbage source (or a provenance row for it).
    q_before = quality_signal(ext.text)
    if q_before["words"] == 0:
        raise ExtractionError(f"{source.name}: extraction produced no text (nothing to ingest)")
    if q_before["control_char_ratio"] > GARBAGE_CTRL_RATIO:
        raise ExtractionError(
            f"{source.name}: extraction looks like binary garbage "
            f"(control-char ratio {q_before['control_char_ratio']:.0%}) — wrong format?")

    dic = load_dict(cfg.dict_path)                        # degrades (not crashes) when absent
    cleaned, stats = process(ext.text, dic)              # FULL normalize, not furniture-only
    q_after = quality_signal(cleaned)
    if q_after["words"] == 0:      # normalize can strip a source to nothing (e.g. all page furniture)
        raise ExtractionError(f"{source.name}: nothing left after normalization "
                              "(source was all furniture/whitespace) — not written")

    # Integrity is measured on `cleaned` — the bytes about to be written — and BEFORE the write, so
    # a measured failure leaves no shelf source behind. Either the artifact is promoted and the exit
    # is success, or it is refused and nothing changed; never both.
    if verify:
        verdict = verify_extraction(cfg, source, ext, cleaned, dic)
        if accept_unverified and verdict.state == integrity.FAILED:
            # An override must KEEP its evidence: downgrade the state, preserve why it failed and
            # the numbers that said so. An override that erases the finding is worse than no check.
            verdict = integrity.Integrity(integrity.UNVERIFIED, verdict.reasons,
                                          verdict.metrics, verdict.thresholds)
    else:
        verdict = integrity.Integrity(integrity.UNVERIFIED, ("verification skipped by caller",),
                                      {}, integrity.Thresholds().as_dict())
    if verdict.blocks_write:
        raise VerificationError(
            f"{source.name}: extraction integrity FAILED — refusing to promote to the shelf.\n  "
            + "\n  ".join(verdict.reasons)
            + "\n  (re-run with --accept-unverified to write it anyway, recorded as unverified)")

    src_sha = sha256(source)       # hash the ORIGINAL bytes BEFORE any write (a forced re-ingest onto
    dest.parent.mkdir(parents=True, exist_ok=True)       # the source itself must record the source hash)
    payload = cleaned.encode("utf-8")                    # encode ONCE and hash exactly these bytes:
    out_sha = hashlib.sha256(payload).hexdigest()        # text-mode newline translation would make a
    #                                                      hash of `cleaned` disagree with the file.

    # Ordering exists to make "success WITH an artifact, or failure with NOTHING changed" true in
    # both directions. Promoting first and recording second left a shelf artifact behind when
    # PROVENANCE.jsonl was unwritable; recording first and promoting second left a durable row for
    # an artifact that never landed. So every fallible step runs BEFORE either side is mutated:
    #
    #   1. write the payload to a temp file in the destination directory (fallible; a dotfile temp
    #      is not the artifact, and it is removed on any failure)
    #   2. append the provenance row (fallible; the shelf is still untouched)
    #   3. os.replace (atomic, same directory — after step 1 succeeded there is no ENOSPC, no
    #      EXDEV, no missing parent left to fail on)
    #
    # If step 3 fails anyway, step 2 is rolled back by truncating the row back off the log — and
    # only when the log is still exactly as this call left it, so a concurrent ingest's row is
    # never destroyed.
    #
    # mkstemp: an UNPREDICTABLE name, created O_EXCL at 0600, in the destination's own directory.
    # A fixed `<dest>.tmp` is guessable, and opening it with write_text FOLLOWS a symlink an
    # attacker planted there, truncating the target. Same defect class already fixed in
    # klode/gate/__main__.py — it was left unfixed here.
    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)

        prov = _provenance_row(cid, shelf, source, src_sha, ext, cleaned, stats, verdict, out_sha,
                               q_before, q_after)
        prov_path = cfg.lib / "PROVENANCE.jsonl"
        line = json.dumps(prov, ensure_ascii=False) + "\n"
        before = prov_path.stat().st_size if prov_path.exists() else 0
        with open(prov_path, "a", encoding="utf-8") as f:
            f.write(line)
        try:
            os.replace(tmp, dest)
        except OSError:
            _rollback_provenance(prov_path, before, len(line.encode("utf-8")))
            raise
    except OSError:
        tmp.unlink(missing_ok=True)                      # don't orphan a partial temp on failure
        raise

    return IngestResult(
        verification=verdict,
        id=cid, shelf=shelf, format=ext.format, handler=ext.handler,
        score_before=round(q_before["corruption"], 2), score_after=round(q_after["corruption"], 2),
        words=len(cleaned.split()), furniture_stripped=stats.get("furniture", 0),
        dest=os.path.relpath(dest, cfg.root),
        provenance=os.path.relpath(prov_path, cfg.root), note=ext.note)


def run(cfg: Config, args) -> int:
    from . import settings as _settings
    source = Path(args.source).expanduser()
    fmt = getattr(args, "format", None)
    if fmt == "auto":
        fmt = None
    try:
        resolved = _settings.resolve(args)          # argument > env > file > default
    except ValueError as e:
        print(f"settings error: {e}", file=sys.stderr)
        return 1
    tier = resolved.value("ingest.tier")
    verify = resolved.value("ingest.verify")
    try:
        r = ingest(cfg, source, args.shelf, card_id=args.id, tier=tier,
                   lang=args.lang, force=args.force, fmt=fmt, verify=verify,
                   accept_unverified=getattr(args, "accept_unverified", False))
    except Exception as e:      # incl. an unsupported format or a failing OCR backend — report, never crash
        print(f"ingest error: {e}", file=sys.stderr)
        return 1
    print(f"ingested {source.name}")
    print(f"  format  : {r.format}  (handler {r.handler}{'; ' + r.note if r.note else ''})")
    print(f"  corruption: {r.score_before} -> {r.score_after} /10k  ({r.words} words)")
    print(f"  furniture : {r.furniture_stripped} lines stripped")
    if r.verification is not None:
        v = r.verification
        print(f"  integrity : {v.state}"
              + (f" — {'; '.join(v.reasons)}" if v.reasons else ""))
        for key in ("containment", "inflation", "order_median", "candidate_missing_pages"):
            if key in v.metrics:
                print(f"      {key}: {v.metrics[key]}")
    print(f"  source  : {r.dest}")
    print(f"  provenance: {r.provenance}")
    print("\nnext: add a BIBLIOGRAPHY row, `klode normalize`, `klode build`, "
          "write the grep-anchored Thin/Full, then `klode check`")
    return 0
