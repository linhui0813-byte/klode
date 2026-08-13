"""Shared low-level helpers: card front-matter, grep-marker extraction, and the
citation-rot matcher. Kept in one place so `build`, `check`, and `cli` cannot drift
in how they parse a card or resolve a `(grep: …)` anchor.
"""
from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .config import Config

# The scaffold marker: everything ABOVE it is machine-managed front-matter + pointers;
# everything BELOW is the hand-written Thin (L1) / Full (L2) body the generator must
# never overwrite. Verbatim from the original design — the anti-drift contract.
MARK = "<!-- scaffold: managed by klode — edit Thin/Full below the marker by hand -->"

# A locating anchor is the SAME thing written several ways, under two key names:
#   (grep: `phrase`)     — key + backtick phrase       — source cards + syntheses
#   (search: `phrase`)   — `search:` is the frameworks layer's name for the same anchor
#   (`grep: "phrase"`)   — whole marker backticked, phrase in double quotes
#   (`grep:` `phrase`)   — key backticked ON ITS OWN, phrase in a separate backtick span
# Each unseen form was a silent hole: a matcher that only knew `grep: ` captured a lone
# space from the last form (9 anchors in one card "passed" by matching whitespace) and
# skipped the entire `search:` frameworks layer — the distilled-expert knowledge the
# library exists to guard. `` `? `` absorbs a backtick that hugs the key on either side;
# `(?<![A-Za-z])` keeps `research:` (any word ending in "search:") from false-matching.
#
# A `-re` suffix (`grep-re:` / `search-re:`) marks the phrase as an INTENTIONAL regex; WITHOUT
# it the phrase is matched LITERALLY (see `resolve`). This split closes the old fail-open where
# a literal quote whose punctuation happened to be a metacharacter was silently retried as a
# regex — a false PASS in the one check that must never fail open.
_KEY_RE = re.compile(r'(?<![A-Za-z])`?(?:grep|search)(-re)?:`?\s*(?:`([^`]+)`|"([^"]+)")')

# Optional W3C-TextQuoteSelector-style disambiguation, parsed from the text IMMEDIATELY after
# the phrase (each optional, any order): pin WHICH occurrence must resolve, by neighbouring
# context or by index. `.match(pos)` (not search) means only tokens adjacent to the phrase attach.
# NB there is deliberately NO leading `` `? `` here: a closing backtick separates the phrase from
# free prose, so skipping it would let a following prose word ("… before …") be misread as context
# (a false citation-rot ERROR). Context sits INSIDE the marker, before any closing backtick.
#   (grep: `phrase` before `words` after `words`)   — prefix/suffix context
#   (grep: `phrase` #2)                              — the 2nd occurrence
_CTX_RE = re.compile(r'\s*(?:(before|after)\s+(?:`([^`]+)`|"([^"]+)")|#(\d+))')

# Several anchors can share one marker, `;`/`|`-separated. Two corpus styles exist:
#   (grep: `A`; `B`)              — B is a BARE phrase with no key of its own → captured HERE
#   (`grep: "A"`; `grep: "B"`)    — each phrase re-states the key → each is a full anchor _KEY_RE
#                                    already finds independently, so _MORE_RE must NOT grab it
# The negative lookahead does exactly that: an extra phrase is captured only when it is NOT itself
# a keyed anchor. It inherits the primary's literal/regex type. The `;`/`|` must sit right after the
# previous phrase, so a `)` closing the marker — or a `;` in prose — never pulls unrelated text in.
_MORE_RE = re.compile(
    r'\s*`?\s*[;|]\s*(?:`(?!`?(?:grep|search)(?:-re)?:)([^`]+)`|"(?!(?:grep|search)(?:-re)?:)([^"]+)")')


@dataclass(frozen=True)
class Marker:
    """One locating anchor: the phrase, whether it is an intentional regex, and any
    occurrence-disambiguation (prefix/suffix context or a 1-based occurrence index)."""
    phrase: str
    regex: bool = False
    before: str | None = None
    after: str | None = None
    nth: int | None = None


# ---- card front-matter ----------------------------------------------------
def front_matter(text: str) -> str:
    # Tolerate CRLF and a closing `---` at end-of-file (no trailing newline) — otherwise a card
    # saved by a Windows editor, or one whose FM block ends the file, parses as having NO
    # front-matter and every downstream check false-fails it.
    m = re.match(r"^---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", text, re.S)
    return m.group(1) if m else ""


def fm_get(fm: str, key: str) -> str | None:
    m = re.search(rf"^{re.escape(key)}:\s*(.+)$", fm, re.M)
    return m.group(1).strip() if m else None


# Recognize the marker written by ANY generation of the generator, not just our own literal.
# The original doxai scripts wrote "managed by build_library_cards.py"; matching only the exact
# klode string made `body_after_marker` miss a real hand-written body, and build then overwrote
# 128 grounded cards with stubs. Match the stable frame; any tool name may sit between.
MARK_RE = re.compile(r"<!-- scaffold: managed by .+? — edit Thin/Full below the marker by hand -->")


def body_after_marker(text: str) -> str | None:
    """The hand-written body below the scaffold marker (Thin/Full), or None."""
    m = MARK_RE.search(text)
    return text[m.end():] if m else None


def parse_markers(text: str) -> list[Marker]:
    """Every locating anchor in `text`, with its type (literal vs regex) and any
    prefix/suffix/occurrence disambiguation attached."""
    out: list[Marker] = []
    for m in _KEY_RE.finditer(text):
        is_re = bool(m.group(1))
        before = after = None
        nth = None
        pos = m.end()
        while (c := _CTX_RE.match(text, pos)):     # only tokens adjacent to the phrase attach
            if c.group(1) == "before":
                before = c.group(2) or c.group(3)
            elif c.group(1) == "after":
                after = c.group(2) or c.group(3)
            elif c.group(4):
                nth = int(c.group(4))
            pos = c.end()
        out.append(Marker(phrase=m.group(2) or m.group(3) or "", regex=is_re,
                          before=before, after=after, nth=nth))
        while (e := _MORE_RE.match(text, pos)):    # additional phrases sharing this marker
            out.append(Marker(phrase=e.group(1) or e.group(2), regex=is_re))
            pos = e.end()
    return out


def grep_markers(text: str) -> list[str]:
    """Every anchor phrase in `text` (type/context dropped) — the phrase-only view for
    callers that just need the strings."""
    return [m.phrase for m in parse_markers(text)]


# ---- citation-rot matcher -------------------------------------------------
# Fold typographic variants so a card's straight-quote quote still matches a source's
# smart quotes / dashes (pdftotext and OCR emit curly forms freely). U+2010 HYPHEN and
# U+2011 NON-BREAKING HYPHEN are visually hyphens some extractors emit instead of ASCII
# '-'; folding them is safe and lossless for a grep match.
_TYPO = {"‘": "'", "’": "'", "“": '"', "”": '"',
         "–": "-", "—": "-", "‐": "-", "‑": "-", "…": "..."}
# Control bytes an extractor drops mid-word ("de\x1fnition") become spaces; kept OUT of \s so
# tab/newline/CR survive to the whitespace collapse below.
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")   # incl. \x7f (DEL), matching config._CTRL_RE


def _norm(s: str) -> str:
    """Collapse whitespace + control bytes to single spaces; fold smart quotes/dashes. The final
    collapse is `" ".join(split())`, measured ~2x faster than an `\\s+` regex sub over a whole
    source — this is the `klode check` hotspot, run over every source (tens of MB) on every run.
    (`.replace` for the rare smart-quote chars beats `str.translate`, which pays a per-char lookup.)"""
    s = CTRL_RE.sub(" ", s)
    for k, v in _TYPO.items():
        s = s.replace(k, v)
    return " ".join(s.split())


_DEHY_RE = re.compile(r"-\s*")


def _dehyphenate(s: str) -> str:
    """Drop hyphens AND the whitespace they introduced, so a line-wrapped `informa-\\ntion`
    (whitespace-folded to `informa- tion`) collapses to the same `information` as the whole
    word. Removing only the hyphen — the old behaviour — left the space and never matched
    the canonical pdftotext line-break case this guard exists to catch."""
    return _DEHY_RE.sub("", s)


def haystacks(txt: str) -> tuple[str, str]:
    flat = _norm(txt)
    nohy = _dehyphenate(flat)             # tolerate any hyphenation / line-break state
    return flat, nohy


@dataclass(frozen=True)
class Resolution:
    found: bool
    count: int          # qualifying occurrences after any context/index filter (0 when absent)
    ambiguous: bool     # a bare literal anchor that resolves in more than one place


def _occurrences(needle: str, hay: str) -> list[int]:
    """Start indices of every non-overlapping occurrence of `needle` in `hay`."""
    out: list[int] = []
    if not needle:
        return out
    start, L = 0, len(needle)
    while (i := hay.find(needle, start)) >= 0:
        out.append(i)
        start = i + L
    return out


def _context_ok(hay: str, i: int, length: int, before: str | None, after: str | None) -> bool:
    """Is the occurrence at `i` immediately preceded by `before` / followed by `after`?
    Everything is whitespace-folded already, so a single boundary space is tolerated —
    the same disambiguation TextQuoteSelector prefix/suffix and text fragments provide."""
    if before:
        pre = hay[:i]
        if not (pre.endswith(before) or pre.endswith(before + " ")):
            return False
    if after:
        post = hay[i + length:]
        if not (post.startswith(after) or post.startswith(" " + after)):
            return False
    return True


def resolve(m: Marker, hays: tuple[str, str]) -> Resolution:
    """Resolve one anchor against a source. LITERAL by default — matches verbatim, folding
    whitespace / hyphenation / smart quotes; REGEX only for an explicit `grep-re:` marker (no
    silent regex fallback — that was a fail-open in the one check that must never fail open).
    Optional `before`/`after` context or an `#n` index pins WHICH occurrence must resolve, so a
    quote that rotted at its intended spot is caught even when a coincidental copy survives."""
    flat, nohy = hays
    if m.regex:
        # A `grep-re:` pattern is the author's OWN, run against their own source — a catastrophic-
        # backtracking pattern can only hang the author's own `klode check`, never a third party.
        # stdlib `re` has no timeout, and a per-anchor thread/subprocess guard would be a heavy price
        # against the zero-dependency ethos for a self-inflicted risk; left as a documented limitation.
        try:
            matches = list(re.finditer(_norm(m.phrase), flat))
        except re.error:
            return Resolution(False, 0, False)
        pinned = bool(m.before or m.after)
        if pinned:                   # honour the pin for regex anchors too, don't silently drop it
            before = _norm(m.before) if m.before else None
            after = _norm(m.after) if m.after else None
            matches = [mm for mm in matches
                       if _context_ok(flat, mm.start(), mm.end() - mm.start(), before, after)]
        if m.nth is not None:
            return Resolution(len(matches) >= m.nth, len(matches), False)
        # A BARE regex is exactly as unpinned as a bare literal, so it earns the same ambiguity
        # report. Hardcoding False here exempted the loosest anchors from the only breadth check
        # there is: `grep-re: .*` resolved as a clean, unambiguous hit and GROUNDED arbitrary text.
        return Resolution(bool(matches), len(matches), len(matches) > 1)

    p = _norm(m.phrase)
    if m.before or m.after:
        # Context can ITSELF be line-wrapped in the source, so resolve the whole thing in the fully
        # de-hyphenated space — it tolerates any hyphenation state for the phrase AND its neighbours.
        # (Doing this only in `flat` false-FAILED a genuine quote whose context word was wrapped.)
        pd = _dehyphenate(p)
        before = _dehyphenate(_norm(m.before)) if m.before else None
        after = _dehyphenate(_norm(m.after)) if m.after else None
        occ = [i for i in _occurrences(pd, nohy)
               if _context_ok(nohy, i, len(pd), before, after)]
        if m.nth is not None:      # honour the `#n` pin here too — don't fail OPEN on a context match
            return Resolution(len(occ) >= m.nth, len(occ), False)
        # Context that STILL leaves several candidates has not disambiguated anything. Reporting
        # False merely because a pin was present was a fail-open: `before: "alpha"` over
        # "alpha target one; alpha target two" resolved as a clean, unique hit. Only `#n` selects
        # unconditionally; every other unpinned outcome is judged by how many places it resolves in.
        return Resolution(bool(occ), len(occ), len(occ) > 1)

    # literal — try the hyphen-preserving space first, then the de-hyphenated (wrapped) space
    occ = _occurrences(p, flat)
    if not occ:
        occ = _occurrences(_dehyphenate(p), nohy)
        if not occ:
            return Resolution(False, 0, False)
    if m.nth is not None:
        return Resolution(len(occ) >= m.nth, len(occ), False)
    return Resolution(True, len(occ), len(occ) > 1)


def occurs(needle: str, hays: tuple[str, str], *, regex: bool = False) -> bool:
    """Does a bare phrase resolve? Literal by default; opt into regex explicitly. A thin
    boolean over `resolve` for the CLI/MCP `--grep` verify path."""
    return resolve(Marker(phrase=needle, regex=regex), hays).found


# ---- enumeration ----------------------------------------------------------
def glob_in(directory, *parts: str) -> list[str]:
    """`glob` rooted at `directory`, with every DIRECTORY segment escaped so that ONLY the final
    pattern is live. Returns `str` paths, unsorted — callers sort where order matters.

    `glob.glob(os.path.join(cfg.cards, "*.md"))` hands the whole joined string to `glob`, which
    reads `[ ] * ?` anywhere in it — the directory prefix included. A library under `.../kb[1]/`
    therefore matched NOTHING and enumerated zero cards, and `check` reported `OK: 0 errors` and
    exited 0 over a citation that resolved nowhere: the `unmeasured` guard only fires `if cards:`,
    so an empty card list is exactly the shape that slips past it. A shelf is a second injection
    point, since config validation rejects only `/`, `""`, `.` and `..` — `shelves = ["books[1]"]`
    loads fine.

    The escape is per SEGMENT, not over the joined path, so a caller may still pass a live pattern
    as the last part (`normalize` takes one from the user, and it may contain `/`).

    Deliberately NOT `Path.glob`, which is immune to the directory problem but is not a
    substitute: it returns dotfiles that `glob` excludes (an editor's `.#card.md` lock file is a
    symlink to `user@host.pid`, which would be read as a card), it treats `**` as recursive where
    `glob` without `recursive=True` does not, it declines to follow directory symlinks, and it
    raises on an absolute pattern that `os.path.join` currently absorbs.
    """
    *dirs, pattern = parts
    root = os.path.join(glob.escape(str(directory)), *(glob.escape(d) for d in dirs))
    return glob.glob(os.path.join(root, pattern))


def src_path_re(cfg: Config) -> re.Pattern[str]:
    """Regex matching a valid shelf-source path (`<lib>/<shelf>/<name>.txt`), built from
    the configured library dir + shelves so it can't false-match a foreign path."""
    shelves = "|".join(re.escape(s) for s in cfg.shelves)
    return re.compile(rf"{re.escape(cfg.lib_rel)}/(?:{shelves})/[A-Za-z0-9._-]+\.txt")


def shelf_txts(cfg: Config) -> list[str]:
    """Every source .txt across every shelf, absolute paths, sorted within each shelf."""
    out: list[str] = []
    for shelf in cfg.shelves:
        out += sorted(glob_in(cfg.lib, shelf, "*.txt"))
    return out


# The generated board and the human README are not cards. One definition, shared by every
# enumerator and by the tripwire below, so a caller cannot disagree with the guard about what
# "no cards" means.
NON_CARDS = ("INDEX.md", "README.md")


def card_files(cfg: Config) -> list[str]:
    """Every card (`cards/<id>.md`), excluding the generated INDEX and README."""
    return [p for p in sorted(glob_in(cfg.cards, "*.md"))
            if os.path.basename(p) not in NON_CARDS]


def read(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def read_lenient(path: str | Path) -> str:
    """Read a possibly-messy source .txt without choking on stray bytes (for grep haystacks)."""
    return Path(path).read_text(encoding="utf-8", errors="replace")
