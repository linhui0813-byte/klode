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
import re
import shutil
import subprocess
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

from .. import coverage
from ._base import Extraction, ExtractionError

CLEAN_THRESHOLD = 5.0          # corruption/10k below which the text layer is trusted (empirical)
MIN_CORRUPTION_MARKERS = 3
"""Markers required before the RATE is believed as evidence of systematic corruption.

The rate is per 10k words, so one marker in a document under 2000 words clears the threshold on its
own — and `testIDs`, `sortBY`, or `fileIOhandler` are ordinary tokens, not OCR damage. The metric
was calibrated on documents scoring 12-51 against clean ones scoring 0-4; those are MANY markers.
Using a single hit to hard-refuse an extraction reads a ratio as a count."""
MIN_WORDS = 200                # guard: an "empty but clean" extraction is not a win
RETENTION_FLOOR = 0.5          # a candidate keeping <50% of the incumbent's words is a
                               # fragment; corruption_score is a ratio and cannot see loss
DOCLING_ENV = "KLODE_DOCLING_URL"    # a docling-serve endpoint, e.g. http://<host>:15001
DOCLING_HTTP_TIMEOUT = 300
MAX_DOCLING_RESPONSE = 64 * 1024 * 1024   # cap the server response bytes read into memory (OOM guard)
MAX_UPLOAD_BYTES = 512 * 1024 * 1024      # cap the INPUT too: the body is read whole and then copied
#                                           into a second full multipart buffer, so a hostile or
#                                           merely huge PDF costs several times its size in RAM
#                                           before the response guard can ever apply.
_TILDE = re.compile(r"[A-Za-z]+~[A-Za-z]+")
_MISCAP = re.compile(r"\b[a-z]{2,}[A-Z]{2}[a-z]*\b")


def corruption_markers(text: str) -> int:
    """How many corruption markers, absolutely — the count `corruption_score` divides away."""
    return len(_TILDE.findall(text)) + len(_MISCAP.findall(text))


def corruption_score(text: str) -> float:
    """OCR-corruption markers per 10k words — tilde-in-word (`t~e`) + mid-word caps (`regulatIOn`).
    The signal that split clean sources from garbled ones.

    NOTE the limit, because the docstring used to claim otherwise: `mfonnafion` is all lowercase
    and `_MISCAP` requires an embedded capital run, so this metric does NOT catch it. Nothing here
    detects a garbled word that happens to be correctly cased — that is `agreement.py`'s job, and
    the reason a second signal exists at all."""
    w = len(text.split()) or 1
    return (len(_TILDE.findall(text)) + len(_MISCAP.findall(text))) / w * 10000


DEFAULT_LANG = "eng"
# Version ranges, because these adapters use specific APIs (kreuzberg's ExtractionConfig fields,
# docling's PdfPipelineOptions). Telling users to install the unconstrained latest made the
# optional environment irreproducible and able to break with no change in this repo.
KREUZBERG_SPEC = "kreuzberg>=4.10,<5"
DOCLING_SPEC = "docling>=2.0,<3"
OCR_TIMEOUT = 1800             # seconds — a local OCR backend has no internal bound; a hostile
#                                or merely enormous PDF must not wedge ingestion forever
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
        raise ImportError(f"xberg/kreuzberg not installed — `pipx inject klode "
                          f"'{KREUZBERG_SPEC}'` (needs the tesseract binary too)") from e
    # `lang` was accepted here and by every other extractor and then used by NONE of them, while
    # `--lang` documented itself as controlling Tier 2/3 OCR. A non-English scan was silently OCR'd
    # as English. Passed through where the backend supports it; where it does not, the caller is
    # told rather than left believing it took effect.
    cfg_kwargs = {"force_ocr": True}
    try:
        r = extract_file_sync(str(pdf), config=ExtractionConfig(ocr_language=lang, **cfg_kwargs))
    except TypeError:
        if lang != DEFAULT_LANG:
            raise RuntimeError(
                f"the installed kreuzberg does not accept an OCR language, so --lang {lang!r} "
                "would be silently ignored. Upgrade kreuzberg, or use --tier docling.")
        r = extract_file_sync(str(pdf), config=ExtractionConfig(**cfg_kwargs))
    return r.content or ""


def _post_multipart_json(pdf: Path, url: str, fields: dict, timeout: float, cap: int) -> dict:
    """One bounded multipart-JSON round trip, shared by both remote backends.

    They had separate copies of: build the body, POST, bounded-read, size-check, decode, and
    top-level type-check — and the copies had ALREADY drifted (one validated nested types, the
    other did not; one stripped a trailing slash, the other did not). Backend-specific schema
    parsing stays with each backend; only the transport is shared.
    """
    boundary = uuid.uuid4().hex
    body = _multipart(fields, "files" if "to_formats" in fields else "file",
                      pdf.name, _read_upload(pdf), boundary)
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(cap + 1)                  # bounded read: never OOM on a huge response
    if len(raw) > cap:
        raise RuntimeError(f"{url}: response exceeds the {cap // 1048576} MB cap")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:     # JSONDecodeError is a ValueError
        raise RuntimeError(f"{url}: unparseable response ({e})") from e
    if not isinstance(data, dict):        # a valid but non-object body (e.g. `[]`) must degrade
        raise RuntimeError(f"{url}: non-object JSON response")
    return data


def _read_upload(pdf: Path) -> bytes:
    """The PDF bytes, size-capped. Only the RESPONSE was bounded, while the request path read the
    whole file and then built another complete copy of it as a multipart body."""
    try:
        size = pdf.stat().st_size
    except OSError as e:
        raise RuntimeError(f"cannot read {pdf.name} ({e})") from e
    if size > MAX_UPLOAD_BYTES:
        raise RuntimeError(f"{pdf.name} is {size / 1048576:.0f} MB, over the "
                           f"{MAX_UPLOAD_BYTES // 1048576} MB upload cap")
    return pdf.read_bytes()


def _multipart(fields: dict, file_field: str, filename: str, file_bytes: bytes,
               boundary: str) -> bytes:
    """Build a multipart/form-data body with stdlib only (no `requests` dependency). The filename
    is stripped of quotes/CR/LF so it cannot inject extra multipart headers."""
    safe_name = filename.replace('"', "").replace("\r", "").replace("\n", "")
    parts: list[bytes] = []
    for k, v in fields.items():
        # a list value becomes a REPEATED field, which is how multipart encodes a list. Joining
        # with a comma produced one value the server can reject as an invalid enum.
        for item in (v if isinstance(v, (list, tuple)) else [v]):
            parts += [f"--{boundary}".encode(),
                      f'Content-Disposition: form-data; name="{k}"'.encode(), b"", str(item).encode()]
    parts += [f"--{boundary}".encode(),
              f'Content-Disposition: form-data; name="{file_field}"; filename="{safe_name}"'.encode(),
              b"Content-Type: application/pdf", b"", file_bytes,
              f"--{boundary}--".encode(), b""]
    return b"\r\n".join(parts)


def docling_endpoint() -> str | None:
    """The docling-serve endpoint, resolved through the normal settings chain: `KLODE_DOCLING_URL`
    first, then `~/.klode/settings.toml`'s `[ingest].docling_url`, then absent.

    ONE resolver, because three call sites each reading `os.environ` and each re-checking the
    scheme is three places for the rule to drift — and one of them had already lost the
    `rstrip("/")` the others had, so a trailing slash produced `…//v1/convert/file`. Scheme
    validation lives in `settings._validate`, which applies it to every source rather than only to
    the environment.

    Returns None — not "" — when unconfigured, so callers can distinguish absent from empty.
    """
    from .. import settings
    return (settings.resolve(None).value("ingest.docling_url") or "").rstrip("/") or None


def _docling_remote(pdf: Path, endpoint: str) -> tuple[str, tuple[int, ...] | None]:
    """`(markdown, pages)` — the escalation path's view. See `_docling_structured`."""
    md, pages, _text = _docling_structured(pdf, endpoint)
    return md, pages


def _docling_structured(pdf: Path,
                        endpoint: str) -> "tuple[str, tuple[int, ...] | None, dict[int, str] | None]":
    """Convert via a docling-serve endpoint (the GPU lives on the server; klode stays zero-dep).
    Returns `(markdown, pages, page_text)` where `pages` is the page numbers the structured result
    claims to cover and `page_text` maps each page to its blocks' text — both None when the
    response carries no provenance. Network/HTTP failure raises OSError and a malformed/oversized
    response raises RuntimeError, so the escalation loop degrades to the best local tier instead of
    crashing.

    `json` is requested alongside `md` so candidate page coverage can be read DIRECTLY from
    `prov[].page_no` rather than inferred from the control — the control cannot answer for the
    candidate, and inferring it was a defect in an earlier design."""
    # repeated field, not "md,json": docling models `to_formats` as a list of enum values, and a
    # single comma-joined value can be rejected — in which case the JSON provenance never arrives
    # and coverage silently degrades to "cannot say".
    data = _post_multipart_json(
        pdf, f"{endpoint}/v1/convert/file",
        {"to_formats": ["md", "json"], "do_table_structure": "true"},
        DOCLING_HTTP_TIMEOUT, MAX_DOCLING_RESPONSE)
    doc = data.get("document") or {}
    if not isinstance(doc, dict):     # a truthy non-dict `document` reached .get and raised
        raise RuntimeError("docling-serve returned a non-object `document`")
    md = doc.get("md_content") or ""
    if not isinstance(md, str):       # a non-string md_content raised AttributeError on .strip()
        raise RuntimeError(f"docling-serve returned {type(md).__name__} markdown, expected a string")
    if not md.strip():
        raise RuntimeError("docling-serve returned no markdown")
    structured = doc.get("json_content")
    if isinstance(structured, str):                       # some builds return it as a JSON string
        try:
            structured = json.loads(structured)
        except ValueError:
            structured = None
    return (md, coverage.pages_from_docling(structured),
            coverage.page_text_from_docling(structured))


def _docling_with_pages(pdf: Path, lang: str = "eng") -> tuple[str, "tuple[int, ...] | None"]:
    """Markdown plus page provenance. `_docling` wraps this for the text-only extractor table."""
    # Resolved ONCE and passed down. Resolving again inside `_docling` meant a settings-file or
    # environment change between the two lookups could send the second call to a different place
    # than the branch decision was made on — and the wrapper would then discard its provenance.
    endpoint = docling_endpoint()
    if endpoint:
        return _docling_remote(pdf, endpoint)
    return _docling_local(pdf, lang), None   # local has a DoclingDocument but no export wired yet


def structured_extract(pdf: Path, tier: str) -> "tuple[str, tuple[int, ...] | None, dict[int, str] | None, str]":
    """`(text, pages, page_text, error)` from ONE backend invocation.

    The harness previously called `_extract()` for the text and then `*_page_text()` for the page
    text — two separate remote conversions of the same document. Beyond paying twice for the most
    expensive step (marker is minutes per document), the two calls are independent nondeterministic
    executions, so `words`/`containment` could describe a different conversion than `visual`. A
    metric and the evidence explaining it must come from the same run.

    Returns an error string rather than raising, because the caller reports which backends it could
    not test rather than quietly testing fewer.
    """
    try:
        if tier == "docling":
            ep = docling_endpoint()
            if ep:
                md, pages, text = _docling_structured(pdf, ep)
                return md, pages, text, ""
            return _docling_local(pdf, DEFAULT_LANG), None, None, ""
        if tier == "marker":
            ep = marker_endpoint()
            if not ep:
                return _marker(pdf, DEFAULT_LANG), None, None, ""    # raises the config error
            md, pages, text = _marker_structured(pdf, ep)
            return md, pages, text, ""
        fn = _EXTRACTORS.get(tier)
        if fn is None:
            return "", None, None, f"unknown tier {tier}"
        return fn(pdf, DEFAULT_LANG), None, None, ""
    except ImportError as e:
        return "", None, None, f"not installed ({e})"
    except (RuntimeError, OSError) as e:
        return "", None, None, f"failed ({e})"


def docling_page_text(pdf: Path) -> "dict[int, str] | None":
    """Per-page candidate text from docling's structured result, or None when it cannot say.

    Only the remote path can answer today: `_docling_remote` requests `json` alongside `md`, so the
    per-block `prov[].page_no` is available. The local `DocumentConverter` path has a
    `DoclingDocument` in hand but no export wired, and inventing page text from markdown would be
    exactly the inference this module refuses to make.
    """
    endpoint = docling_endpoint()
    if not endpoint:
        return None
    return _docling_structured(pdf, endpoint)[2]


def _docling(pdf: Path, lang: str = "eng") -> str:
    """Markdown only — the escalation loop compares text and nothing else."""
    endpoint = docling_endpoint()
    if endpoint:                                          # remote docling-serve (GPU lives server-side)
        return _docling_remote(pdf, endpoint)[0]
    return _docling_local(pdf, lang)


def _docling_local(pdf: Path, lang: str = "eng") -> str:
    """The in-process backend. Split out so the endpoint is resolved exactly once, by the caller."""
    try:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions
        from docling.datamodel.base_models import InputFormat
    except ImportError as e:
        raise ImportError(f"docling not installed — set $KLODE_DOCLING_URL to a docling-serve "
                          f"endpoint, or `pipx inject klode '{DOCLING_SPEC}'` "
                          "(heavy: torch + models).") from e
    opts = PdfPipelineOptions()
    opts.do_ocr = True
    try:
        opts.force_full_page_ocr = True
        opts.ocr_options = TesseractCliOcrOptions()      # match xberg's engine; skip the Chinese default
    except AttributeError as e:
        # ONLY the documented compatibility case: an older docling without these fields. A blanket
        # `except Exception: pass` also swallowed a missing tesseract binary, an invalid option, and
        # any programming error here — every one of which silently changes which OCR engine runs,
        # and docling's default is a Chinese model that produces nonsense on English.
        raise RuntimeError(
            f"docling is installed but does not accept the OCR options klode requires ({e}). "
            "Its default OCR backend is a Chinese model and would produce nonsense on English "
            "text, so klode refuses to run it unconfigured — upgrade docling, or use a "
            "docling-serve endpoint via [ingest].docling_url.") from e
    conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    return conv.convert(str(pdf)).document.export_to_markdown()


# --------------------------------------------------------------------------- marker
#
# marker is REMOTE-ONLY here, and deliberately NOT in the `auto` escalation ladder. A backend earns
# a ladder slot by measuring better than the one it would displace (`eval/extract_bakeoff.py`) —
# the same rule that kept BM25 out of retrieval until an eval set existed. Until that measurement
# says otherwise, `--tier marker` runs it on request and the bake-off ranks it; nothing escalates
# to it on its own.

MARKER_ENV = "KLODE_MARKER_URL"          # a `marker_server` endpoint, e.g. http://<host>:15002
MARKER_HTTP_TIMEOUT = 900                # marker is minutes-per-document on a large PDF, not seconds
MAX_MARKER_RESPONSE = 64 * 1024 * 1024
# `{N}` then a rule, emitted by `paginate_output=true`. 0-INDEXED, and it precedes each page.
_MARKER_PAGE_RE = re.compile(r"^\{(\d+)\}-{8,}$", re.M)


def marker_endpoint() -> str | None:
    """The marker_server endpoint, resolved through the settings chain. See `docling_endpoint`."""
    from .. import settings
    return (settings.resolve(None).value("ingest.marker_url") or "").rstrip("/") or None


def _marker_mode() -> str:
    from .. import settings
    return settings.resolve(None).value("ingest.marker_mode")


def split_marker_pages(md: str, page_ids: "list[int] | None" = None) -> "dict[int, str] | None":
    """Per-page text from marker's paginated markdown, 1-indexed, or None when it cannot say.

    marker emits `{N}` + a rule BEFORE each page, with N **0-indexed**; klode counts pages from 1
    everywhere else, so the offset is corrected here rather than at each call site.

    `page_ids` is marker's own `metadata.page_stats[].page_id` — an INDEPENDENT statement of which
    pages it produced. When the two disagree, this returns None. Reconciling them would mean
    picking a winner between two sources that just contradicted each other, which is a guess; the
    caller's honest answer is "cannot say" and coverage already distinguishes that from "complete".
    """
    hits = list(_MARKER_PAGE_RE.finditer(md))
    if not hits:
        return None                       # not paginated — never infer boundaries from headings
    out: dict[int, str] = {}
    for i, m in enumerate(hits):
        stop = hits[i + 1].start() if i + 1 < len(hits) else len(md)
        page = int(m.group(1)) + 1
        if page in out:
            # two separators claiming the same page: the later silently overwrote the earlier, and
            # a contradictory pagination was then reported as valid provenance
            return None
        out[page] = md[m.end():stop].strip()
    if page_ids is not None and sorted(out) != sorted(n + 1 for n in page_ids):
        return None                       # marker's two accounts of its own output disagree
    return out or None


def _marker_structured(pdf: Path,
                       endpoint: str) -> "tuple[str, tuple[int, ...] | None, dict[int, str] | None]":
    """`(markdown, pages, page_text)` from a `marker_server` endpoint.

    `paginate_output` is always requested: without it marker returns one undivided markdown blob,
    the visual check cannot align a page, and the backend becomes unrankable — the exact hole that
    made docling unscorable on every document until its structured result was read.
    """
    data = _post_multipart_json(
        pdf, f"{endpoint}/marker/upload",
        {"paginate_output": "true", "output_format": "markdown", "mode": _marker_mode()},
        MARKER_HTTP_TIMEOUT, MAX_MARKER_RESPONSE)
    if not data.get("success"):
        # marker reports failure with HTTP 200 and `success: false`. Reading only the status code
        # would take an error payload for a document.
        raise RuntimeError(f"marker_server failed: {str(data.get('error'))[:200]}")
    md = data.get("output") or ""
    if not isinstance(md, str):
        raise RuntimeError(f"marker_server returned {type(md).__name__} output, expected a string")
    if not md.strip():
        raise RuntimeError("marker_server returned no text")
    meta = data.get("metadata") or {}
    if not isinstance(meta, dict):    # a truthy non-dict metadata raised AttributeError on .get
        raise RuntimeError("marker_server returned a non-object `metadata`")
    stats = meta.get("page_stats") or []
    if not isinstance(stats, list):
        raise RuntimeError("marker_server returned a non-list `page_stats`")
    ids = [s["page_id"] for s in stats
           if isinstance(s, dict) and isinstance(s.get("page_id"), int)] or None
    text = split_marker_pages(md, ids)
    return md, (tuple(sorted(text)) if text else None), text


def marker_page_text(pdf: Path) -> "dict[int, str] | None":
    endpoint = marker_endpoint()
    return _marker_structured(pdf, endpoint)[2] if endpoint else None


def _marker(pdf: Path, lang: str = "eng") -> str:
    endpoint = marker_endpoint()
    if not endpoint:
        raise ImportError(
            f"marker not configured — set ${MARKER_ENV} (or [ingest].marker_url in "
            "~/.klode/settings.toml) to a `marker_server` endpoint. There is no local path: "
            "marker pulls torch + layout models, which klode does not depend on.")
    return _marker_structured(pdf, endpoint)[0]


def _marker_with_pages(pdf: Path, lang: str = "eng") -> tuple[str, "tuple[int, ...] | None"]:
    endpoint = marker_endpoint()
    if not endpoint:
        return _marker(pdf, lang), None          # raises the configuration error above
    md, pages, _text = _marker_structured(pdf, endpoint)
    return md, pages


_EXTRACTORS = {"pdftotext": _pdftotext, "xberg": _xberg, "docling": _docling,
               "marker": _marker}
_PAGE_AWARE = {"docling": _docling_with_pages, "marker": _marker_with_pages}
"""Backends that can report which pages they represent. One table, so a forced tier cannot
silently take the text-only extractor and discard the provenance."""
# `marker` is selectable and rankable but NOT in the `auto` ladder — see the marker section above.


@dataclass
class Choice:
    tier: str
    text: str
    score: float
    note: str = ""
    pages: "tuple[int, ...] | None" = None
    """Page provenance from a structured backend, or None when it cannot say.

    Carried on the result rather than in a module-global keyed by path: that global raced when the
    same path was ingested concurrently (one call could consume another's provenance) and leaked
    entries whenever docling ran during `auto` but lost."""


def _forced(pdf: Path, tier: str, lang: str) -> Choice:
    """A tier the caller named. Returns whatever it produced — no quality refusal, because only
    `auto` claims to have chosen on quality."""
    fn = _EXTRACTORS.get(tier)
    if fn is None:
        raise ValueError(f"unknown tier {tier!r}; choose one of {', '.join(_EXTRACTORS)}")
    if tier in _PAGE_AWARE:
        # both carry page provenance; taking the text-only extractor would discard it
        text, pages = _PAGE_AWARE[tier](pdf, lang)
        return Choice(tier, text, corruption_score(text), pages=pages)
    text = fn(pdf, lang)
    return Choice(tier, text, corruption_score(text))


def _better(cur: Choice, name: str, text: str, note: str, pages=None) -> Choice:
    """Replace the incumbent only on evidence.

    Two guards, because `corruption_score` cannot supply either on its own:

    - RETENTION. The comment said it rejected "EMPTY/short" output and the code tested only EMPTY,
      so a one-word OCR result — which scores 0.0, there being no corruption markers in one word to
      find — displaced hundreds of words and suppressed further escalation. `corruption_score` is a
      RATIO over the words present and structurally cannot see loss. The floor is relative to the
      incumbent, not an absolute count: gating on MIN_WORDS too rejected a clean 120-word recovery
      of a corrupted 120-word document, which is a fact about the document, not the extraction.
    - STRICTLY LOWER. `<=` let an equally-scored backend displace the incumbent for no measured
      reason, and repeated ties walked the ladder to its last rung.
    """
    words, cur_words = len(text.split()), len(cur.text.split())
    if not words:
        return cur
    if cur_words and words < cur_words * RETENTION_FLOOR:
        return cur                                        # a fragment, not an improvement
    score = corruption_score(text)
    if not cur_words or score < cur.score:
        return Choice(name, text, score, note, pages=pages)
    return cur


def _tier1(pdf: Path, lang: str) -> tuple[Choice, bool]:
    """(candidate, done). A missing or failing Poppler is an escalation reason, not an abort."""
    try:
        t1 = _pdftotext(pdf, lang)
        s1 = corruption_score(t1)
        if s1 < CLEAN_THRESHOLD and len(t1.split()) >= MIN_WORDS:
            return Choice("pdftotext", t1, s1, "text layer clean"), True
        return Choice("pdftotext", t1, s1, f"pdftotext scored {s1:.1f}"), False
    except (RuntimeError, OSError) as e:
        return Choice("pdftotext", "", float("inf"), f"pdftotext failed ({e})"), False


def _escalate(best: Choice, pdf: Path, lang: str) -> Choice:
    """Tier 2 then, only if still unusable, Tier 3. Backend absence is a note, never a crash."""
    try:
        best = _better(best, "xberg", _xberg(pdf, lang),
                       f"escalated: pdftotext scored {best.score:.1f}")
    except ImportError as e:
        best = Choice(best.tier, best.text, best.score, f"WANTED OCR but {e}", pages=best.pages)
    except (RuntimeError, OSError) as e:                  # backend runtime failure, not a bug
        best = Choice(best.tier, best.text, best.score,
                      f"{best.note}; xberg failed ({e})", pages=best.pages)

    if best.text.split() and best.score < CLEAN_THRESHOLD:
        return best
    try:
        # `_docling_with_pages`, not `_docling`: the auto path used the text-only extractor, so an
        # auto-escalated docling win arrived with `pages=None` even when the backend had supplied
        # per-block provenance — and coverage then abstained on evidence it had.
        md, pages = _docling_with_pages(pdf, lang)
        return _better(best, "docling", md,
                       f"escalated to docling: prior scored {best.score:.1f}", pages=pages)
    except (ImportError, RuntimeError, OSError) as e:
        return Choice(best.tier, best.text, best.score,
                      f"{best.note}; docling absent ({e})", pages=best.pages)


def choose_and_extract(pdf: Path, tier: str = "auto", lang: str = "eng") -> Choice:
    """Pick a tier. Forced tiers run as asked; `auto` uses the cheap path unless its measured
    corruption says otherwise, then escalates pdftotext -> xberg -> docling, taking the best.

    Split into `_forced` / `_tier1` / `_escalate` / `_better` because the single 94-line version
    interleaved dispatch, scoring, orchestration and error translation — and the inconsistency that
    produced (a usability check applied on one path and not the other) is exactly where the
    one-word-fragment defect lived.
    """
    if tier != "auto":
        return _forced(pdf, tier, lang)
    best, done = _tier1(pdf, lang)
    if done:
        return best
    _tier1_text = best.text          # kept: it is the only corroboration a short result can have
    best = _escalate(best, pdf, lang)

    # `auto` used to return `best` even when EVERY tier failed the criteria `auto` itself judges
    # by. A 15-word extraction scoring 6667 was handed back as the chosen result, and verification
    # then ABSTAINED — the control tier IS pdftotext, so there is nothing to compare against — and
    # the garbage was promoted with no check having run. Gated on CORRUPTION only: MIN_WORDS
    # belongs to the tier-1 fast path and cannot mean "this document is too short to be real".
    words = len(best.text.split())
    # Two independent conditions, because `corruption_score` is a RATIO and a short clean-looking
    # result scores 0.0 for the same reason an empty one does — there are no markers to find.
    #
    #   corruption  — the text is garbled
    #   substance   — there is not enough of it to have measured anything
    #
    # The substance floor applies ONLY when no control corroborates the result. `_better`'s
    # retention rule is relative to an incumbent, so with pdftotext failed (`cur_words == 0`) a
    # single clean token became `best` and cleared the corruption gate. A short document extracted
    # by a WORKING control is still fine — that case has corroboration and is not refused.
    corroborated = bool(_tier1_text and len(_tier1_text.split()) >= words * RETENTION_FLOOR)
    # BOTH the rate and the count: one ordinary identifier in a short document clears the rate by
    # itself, and refusing a correct extraction over a single `testIDs` is the same over-correction
    # the MIN_WORDS gate already made once.
    garbled = (best.score >= CLEAN_THRESHOLD
               and corruption_markers(best.text) >= MIN_CORRUPTION_MARKERS)
    if not words or garbled or (words < MIN_WORDS and not corroborated):
        why = ("garbled" if garbled
               else "empty" if not words else "too little text, and no control to corroborate it")
        raise ExtractionError(
            f"{pdf.name}: no extraction tier reached usable quality — {why} "
            f"(best: {best.tier}, {words} words, corruption {best.score:.1f}, "
            f"needs < {CLEAN_THRESHOLD}). {best.note}. "
            f"Install an OCR tier, point --tier at a remote backend, or force "
            f"`--tier {best.tier}` to accept this text deliberately.")
    return best

class PdfHandler:
    name = "pdf"
    format = "pdf"
    priority = 5

    def sniff(self, path: Path, head: bytes) -> bool:
        return head.startswith(b"%PDF")

    def extract(self, path: Path, *, lang: str = "eng", tier: str = "auto") -> Extraction:
        c = choose_and_extract(path, tier, lang)
        return Extraction(text=c.text, handler=c.tier, format="pdf", note=c.note, pages=c.pages)
