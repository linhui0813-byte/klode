"""DOCX handler — pure stdlib. Reads `word/document.xml` in memory (SafeZip: zip-bomb +
traversal guards) and walks the OOXML `w:` namespace: concatenate `<w:t>` runs within a
`<w:p>`, one line per paragraph. Optional richer extraction (python-docx: tables/footnotes)
is opt-in via `tier="python-docx"`, lazy-imported, and still gated through SafeZip first."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from ._base import Extraction, ExtractionError, SafeZip, ZipBombError, ZipTraversalError

_DOCUMENT = "word/document.xml"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]      # strip the OOXML namespace: "{w}t" -> "t"


def _stdlib_text(document_xml: bytes) -> str:
    try:
        root = ET.fromstring(document_xml)
    except ET.ParseError as e:
        raise ExtractionError(f"docx: malformed word/document.xml ({e})") from e
    lines: list[str] = []
    for para in root.iter():
        if _local(para.tag) != "p":
            continue
        buf: list[str] = []
        for node in para.iter():
            kind = _local(node.tag)
            if kind == "t":
                buf.append(node.text or "")
            elif kind == "tab":
                buf.append("\t")
            elif kind == "br":
                buf.append("\n")
        lines.append("".join(buf))
    return "\n".join(lines)


class DocxHandler:
    name = "docx"
    format = "docx"
    priority = 20

    def sniff(self, path: Path, head: bytes) -> bool:
        if not head.startswith(b"PK\x03\x04"):
            return False
        try:
            with SafeZip(path) as z:
                return z.has(_DOCUMENT)
        except (ZipBombError, ZipTraversalError):
            raise                                        # a malicious archive fails loud, not "no match"
        except Exception:
            return False

    def extract(self, path: Path, *, lang: str = "eng", tier: str = "auto") -> Extraction:
        # Always screen the archive through SafeZip (zip-bomb + traversal) BEFORE any reader —
        # including the optional python-docx path, which would otherwise bypass the guards.
        with SafeZip(path) as z:
            if not z.has(_DOCUMENT):
                raise ExtractionError("docx: missing word/document.xml")
            document_xml = z.read(_DOCUMENT)
        if tier == "python-docx":                        # opt-in richer extraction (post-screening)
            try:
                import docx as _docx                      # lazy: python-docx, absent by default
                doc = _docx.Document(str(path))
                text = "\n".join(p.text for p in doc.paragraphs)
                return Extraction(text=text, handler="python-docx", format="docx",
                                  note="extracted via python-docx")
            except ImportError:
                pass                                     # fall through to the stdlib path
        return Extraction(text=_stdlib_text(document_xml), handler="docx", format="docx")
