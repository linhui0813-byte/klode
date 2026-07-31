"""EPUB handler — pure stdlib. Reads the archive in memory (SafeZip: zip-bomb + traversal
guards), resolves the OPF, and extracts each XHTML chapter IN SPINE READING ORDER (not zip or
alphabetical order), reusing the HTML text engine."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from ._base import Extraction, ExtractionError, SafeZip, ZipBombError, ZipTraversalError
from .html import html_to_text

_CONTAINER = "META-INF/container.xml"
_MIMETYPE = "application/epub+zip"
_OPF_MEDIA = "application/oebps-package+xml"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]      # strip an XML namespace: "{ns}item" -> "item"


def _parse(data: bytes, what: str) -> ET.Element:
    try:
        return ET.fromstring(data)
    except ET.ParseError as e:
        raise ExtractionError(f"epub: malformed {what} ({e})") from e


def _resolve(base: PurePosixPath, href: str) -> str:
    """Resolve a manifest href to a ZIP entry name: percent-decode, drop query/fragment, and
    normalize against the OPF's directory (so `../` and encoded paths land on the real entry)."""
    href = unquote(href).split("#", 1)[0].split("?", 1)[0]
    return str((base / href))


class EpubHandler:
    name = "epub"
    format = "epub"
    priority = 10

    def sniff(self, path: Path, head: bytes) -> bool:
        if not head.startswith(b"PK\x03\x04"):
            return False
        try:
            with SafeZip(path) as z:
                return z.has("mimetype") and z.read_text("mimetype").strip() == _MIMETYPE
        except (ZipBombError, ZipTraversalError):
            raise                                        # a malicious archive fails loud, not "no match"
        except Exception:
            return False

    def extract(self, path: Path, *, lang: str = "eng", tier: str = "auto") -> Extraction:
        with SafeZip(path) as z:
            if not z.has(_CONTAINER):
                raise ExtractionError("epub: missing META-INF/container.xml")
            container = _parse(z.read(_CONTAINER), "container.xml")
            rootfiles = [el for el in container.iter()
                         if _local(el.tag) == "rootfile" and el.get("full-path")]
            opf_path = next((el.get("full-path") for el in rootfiles
                             if el.get("media-type") == _OPF_MEDIA), None)
            if opf_path is None and rootfiles:           # fall back to the first if none is typed
                opf_path = rootfiles[0].get("full-path")
            if not opf_path or not z.has(opf_path):
                raise ExtractionError("epub: container.xml has no valid OPF rootfile")
            opf = _parse(z.read(opf_path), "OPF")
            base = PurePosixPath(opf_path).parent
            manifest = {el.get("id"): el.get("href") for el in opf.iter()
                        if _local(el.tag) == "item" and el.get("id") and el.get("href")}
            spine = [el.get("idref") for el in opf.iter()
                     if _local(el.tag) == "itemref" and el.get("idref")]
            if not spine:
                raise ExtractionError("epub: spine is empty")
            parts: list[str] = []
            for sid in spine:                            # SPINE order — the reading order
                href = manifest.get(sid)
                if not href:
                    continue
                entry = _resolve(base, href)             # percent-decoded, query/fragment stripped
                if z.has(entry):
                    parts.append(html_to_text(z.read(entry)))
        text = "\n\n".join(p for p in parts if p.strip())
        return Extraction(text=text, handler="epub", format="epub")
