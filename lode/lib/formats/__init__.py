"""The format registry + the content-sniffing router.

One `FormatHandler` per format (mirrors the `opspec.py` declarative-registry pattern); `route()`
picks a handler by CONTENT (a signature sniff of the file head), not by extension, so a `.txt`
that is really a PDF — or an `.epub` that is a bare zip — is classified correctly. `--format`
forces a handler. Importing this package pulls in NO optional backend (all lazy-imported)."""
from __future__ import annotations

from pathlib import Path

from ._base import (Extraction, ExtractionError, FormatHandler, MAX_UNCOMPRESSED, SafeZip,
                    UnsupportedFormat, ZipBombError, ZipTraversalError, looks_markup)  # noqa: F401
from .docx import DocxHandler
from .epub import EpubHandler
from .html import HtmlHandler
from .pdf import PdfHandler
from .txt import TxtHandler

__all__ = ["Extraction", "FormatHandler", "ExtractionError", "UnsupportedFormat",
           "ZipBombError", "ZipTraversalError", "SafeZip", "MAX_UNCOMPRESSED",
           "HANDLERS", "by_format", "route"]

_HEAD_BYTES = 512

# priority order (low = tried first): pdf < epub < docx < html < txt-fallback
HANDLERS: tuple[FormatHandler, ...] = tuple(sorted(
    (PdfHandler(), EpubHandler(), DocxHandler(), HtmlHandler(), TxtHandler()),
    key=lambda h: h.priority))


def by_format(fmt: str) -> FormatHandler | None:
    return next((h for h in HANDLERS if h.format == fmt), None)


def route(path, *, fmt: str | None = None) -> FormatHandler:
    """Return the handler for `path`. `fmt` (from `--format`) forces one; otherwise sniff the
    head against each handler in priority order. Raise `UnsupportedFormat` when nothing matches
    — never silently default to text for binary/empty input."""
    path = Path(path)
    if fmt is not None and fmt != "auto":
        h = by_format(fmt)
        if h is None:
            raise UnsupportedFormat(f"unknown format {fmt!r}")
        return h
    with open(path, "rb") as f:              # read only the head, never the whole (possibly huge) file
        head = f.read(_HEAD_BYTES)
    for h in HANDLERS:
        try:
            if h.sniff(path, head):
                return h
        except (ZipBombError, ZipTraversalError):
            raise                            # a malicious archive fails loud, not "unsupported"
        except Exception:
            continue                         # a handler that errors while sniffing simply does not match
    raise UnsupportedFormat(f"no handler recognized {path.name} (pass --format to force one)")
