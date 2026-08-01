"""Plain-text handler — the fallback. Decodes bytes to UTF-8 (UTF-16-BOM and latin-1 fallbacks),
strips a BOM. Its `sniff` REJECTS binary/markup heads (and heads that are mostly control bytes)
so a PDF/zip/HTML or raw binary never falls through to text."""
from __future__ import annotations

from pathlib import Path

from ._base import Extraction, looks_markup

_BINARY_SIGS = (b"%PDF", b"PK\x03\x04")
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


class TxtHandler:
    name = "txt"
    format = "txt"
    priority = 100                # the fallback: lowest precedence (highest number)

    def sniff(self, path: Path, head: bytes) -> bool:
        if not head:              # empty is not a text source (route -> unsupported)
            return False
        if head.startswith(_UTF16_BOMS):                 # BOM'd UTF-16 text
            return True
        if head.startswith(_BINARY_SIGS) or looks_markup(head) or b"\x00" in head:
            return False
        sample = head[:256]                              # a text head is mostly printable/whitespace
        printable = sum(1 for b in sample if b >= 0x20 or b in (0x09, 0x0a, 0x0d))
        return printable / len(sample) >= 0.85

    def extract(self, path: Path, *, lang: str = "eng", tier: str = "auto") -> Extraction:
        raw = path.read_bytes()
        note = ""
        if raw[:2] in _UTF16_BOMS:
            text = raw.decode("utf-16")
            note = "decoded as utf-16 (BOM)"
        else:
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("latin-1")
                note = "decoded as latin-1 (not valid UTF-8)"
        if text[:1] == "﻿":  # strip a UTF-8 BOM so grep/anchors don't trip on it
            text = text[1:]
        return Extraction(text=text, handler="txt", format="txt", note=note)
