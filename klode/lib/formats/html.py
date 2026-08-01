"""HTML/XHTML handler — a lenient `html.parser` subclass that drops non-content elements,
breaks at block-level tags, and lets `convert_charrefs` unescape entities. Also the text engine
EPUB reuses for its XHTML chapters. Optional web-article boilerplate removal (trafilatura) is
opt-in via `tier="trafilatura"` and lazy-imported — absent by default, never blocking stdlib."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from ._base import Extraction, looks_markup

_BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article",
          "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote",
          "pre", "hr", "dd", "dt", "figure", "figcaption", "nav", "main", "aside"}
_SKIP = {"script", "style", "head", "title", "meta", "link", "noscript"}


class HTMLTextExtractor(HTMLParser):
    """Accumulate visible text, inserting a newline at each block boundary and skipping the
    body of non-content tags (script/style/…). `convert_charrefs=True` unescapes entities."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip_depth += 1
        elif tag in _BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK:
            self._parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCK:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        lines = [" ".join(ln.split()) for ln in raw.split("\n")]   # collapse intra-line whitespace
        out: list[str] = []
        blank = 0
        for ln in lines:
            if ln:
                out.append(ln)
                blank = 0
            else:
                blank += 1
                if blank <= 1 and out:      # keep at most one blank line between paragraphs
                    out.append(ln)
        return "\n".join(out).strip("\n")


def html_to_text(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):           # UTF-16 BOM
        src = data.decode("utf-16")
    else:
        try:
            src = data.decode("utf-8")
        except UnicodeDecodeError:
            src = data.decode("cp1252", "replace")       # the legacy-web default, superset of latin-1
    if src[:1] == "﻿":
        src = src[1:]
    p = HTMLTextExtractor()
    p.feed(src)
    p.close()
    return p.text()


class HtmlHandler:
    name = "html"
    format = "html"
    priority = 30

    def sniff(self, path: Path, head: bytes) -> bool:
        return looks_markup(head)

    def extract(self, path: Path, *, lang: str = "eng", tier: str = "auto") -> Extraction:
        if tier == "trafilatura":                        # opt-in web-article extraction
            try:
                import trafilatura                        # lazy: absent by default
                txt = trafilatura.extract(path.read_text(encoding="utf-8", errors="replace"))
                if txt:
                    return Extraction(text=txt, handler="trafilatura", format="html",
                                      note="boilerplate-stripped (trafilatura)")
            except ImportError:
                pass                                     # fall through to the stdlib path
        return Extraction(text=html_to_text(path.read_bytes()), handler="html", format="html")
