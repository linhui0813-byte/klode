"""Shared types + safe-zip primitives for the format handlers.

Imports ONLY stdlib and no OCR/parse backend at module load, so `import klode.lib.formats`
stays backend-free — the default test suite runs with zero optional packages installed.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol, runtime_checkable

# Cumulative decompressed-bytes cap for one archive (zip-bomb guard). Generous for real
# books, small enough to stop a bomb. Enforced on the DECLARED uncompressed size — which
# stdlib's reader will not exceed — AND as a streaming belt-and-suspenders cap. Tests lower
# it via monkeypatch, so handlers read it at call time, never bind it at import.
MAX_UNCOMPRESSED = 512 * 1024 * 1024      # 512 MiB

_BOM = b"\xef\xbb\xbf"
_MARKUP_STARTS = (b"<?xml", b"<!doctype html", b"<html")


class ExtractionError(Exception):
    """A source could not be extracted to text."""


class UnsupportedFormat(ExtractionError):
    """No handler recognized the source (and none was forced)."""


class ZipBombError(ExtractionError):
    """An archive's declared or streamed uncompressed size exceeds the cap."""


class ZipTraversalError(ExtractionError):
    """An archive entry name escapes the archive root (`..` or absolute)."""


def looks_markup(head: bytes) -> bool:
    """True when the head (after an optional BOM + leading whitespace) opens an XML/HTML doc."""
    s = head[3:] if head[:3] == _BOM else head
    return s.lstrip().lower().startswith(_MARKUP_STARTS)


@dataclass(frozen=True)
class Extraction:
    """The neutral output every handler yields: raw extracted text + which handler/format
    produced it. Immutable — a result is a fact, not a mutable buffer."""
    text: str
    handler: str
    format: str
    note: str = ""
    pages: tuple[int, ...] | None = None
    """Page numbers this extraction claims to represent, when the backend can say.

    `None` means UNKNOWN, not empty: a markdown-only backend genuinely cannot report which pages it
    covered, and treating that as "no pages missing" is the exact inference this field exists to
    stop. Only a structured result (docling `prov[].page_no`) populates it."""

    def __post_init__(self) -> None:
        if not self.format:
            raise ValueError("Extraction.format must be non-empty")


@runtime_checkable
class FormatHandler(Protocol):
    name: str
    format: str
    priority: int
    def sniff(self, path: Path, head: bytes) -> bool: ...
    def extract(self, path: Path, *, lang: str = "eng", tier: str = "auto") -> Extraction: ...


def _safe_entry_name(name: str) -> None:
    p = PurePosixPath(name)
    if (name.startswith(("/", "\\")) or p.is_absolute() or ".." in p.parts
            or (len(name) >= 2 and name[1] == ":")):     # drive-letter (Windows absolute)
        raise ZipTraversalError(f"unsafe zip entry name: {name!r}")


class SafeZip:
    """Read entries from a zip IN MEMORY with zip-bomb + path-traversal guards. Never extracts
    to disk. Rejects the whole archive up front if ANY entry name escapes the root, so a
    malicious entry is refused whether or not a handler happens to read it."""

    def __init__(self, path: Path, cap: int | None = None):
        self.zf = zipfile.ZipFile(path)
        try:
            for n in self.zf.namelist():
                _safe_entry_name(n)
        except Exception:
            self.zf.close()
            raise
        self._cap = cap
        self._read_total = 0

    @property
    def cap(self) -> int:
        return self._cap if self._cap is not None else MAX_UNCOMPRESSED

    def names(self) -> list[str]:
        return self.zf.namelist()

    def has(self, name: str) -> bool:
        return name in self.zf.namelist()

    def read_text(self, name: str, *, encoding: str = "utf-8") -> str:
        return self.read(name).decode(encoding, "replace")

    def read(self, name: str) -> bytes:
        _safe_entry_name(name)
        info = self.zf.getinfo(name)
        # declared-size guard: stdlib's reader will not return more than file_size, so the real
        # bomb (tiny compressed -> huge declared inflate) is caught here, cumulatively.
        if info.file_size > self.cap or self._read_total + info.file_size > self.cap:
            raise ZipBombError(f"zip entry {name!r} exceeds the {self.cap}-byte cap "
                               f"(declared {info.file_size}, {self._read_total} already read)")
        out = bytearray()
        with self.zf.open(name) as f:                    # streaming belt-and-suspenders cap
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                out += chunk
                self._read_total += len(chunk)
                if self._read_total > self.cap:
                    raise ZipBombError(f"archive exceeds the {self.cap}-byte cap while reading {name!r}")
        return bytes(out)

    def close(self) -> None:
        self.zf.close()

    def __enter__(self) -> "SafeZip":
        return self

    def __exit__(self, *a) -> None:
        self.close()
