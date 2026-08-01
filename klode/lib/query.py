"""Pure, side-effect-free query functions over a library.

`cli` renders these for a human at a terminal; `mcp_server` renders them for an agent.
Keeping the logic here means the two surfaces cannot drift in how they section, rank,
or verify a card — the same reason `common` exists for card parsing.

Nothing in this module prints, exits, or writes. Every function returns data.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .common import card_files, fm_get, front_matter, haystacks, occurs, read
from .config import Config

_SECTION_RE = re.compile(r"^##\s+(.+?)\s*\n(.*?)(?=^##\s|\Z)", re.M | re.S)
_TITLE_RE = re.compile(r"^#\s+(.+)$", re.M)


# ---- card anatomy ---------------------------------------------------------
def sections(card_text: str) -> dict[str, str]:
    """Map each `## Heading` to its body (the hand-written Thin/Full live here)."""
    return {m.group(1).strip().lower(): m.group(2).strip()
            for m in _SECTION_RE.finditer(card_text)}


def title(card_text: str) -> str:
    m = _TITLE_RE.search(card_text)
    return m.group(1).strip() if m else ""


def card_path(cfg: Config, card_id: str) -> Path | None:
    """Absolute path to a card, or None when no such card exists. A card id is a bare filename
    stem: reject any path separator (the same `_SAFE_STEM` rule `resolve`/`dimension` use) so a
    tool-supplied id — `zoom_card`/`verify_quote` take untrusted ids over MCP — cannot traverse
    out of the cards dir to read an arbitrary `.md`."""
    if not _SAFE_STEM.fullmatch(card_id or ""):
        return None
    p = cfg.cards / f"{card_id}.md"
    return p if p.exists() else None


# ---- search (L0/L1 retrieval) ---------------------------------------------
@dataclass(frozen=True)
class Hit:
    id: str
    score: float
    zoom: str
    shelf: str
    gist: str


# BM25 constants: k1 controls term-frequency saturation, b the strength of length
# normalization (textbook defaults). The point of P4 is *having* length normalization at all:
# the old `sum(count)` buried a short, on-point card under a long one that merely repeated a term.
_BM25_K1 = 1.5
_BM25_B = 0.75


def search(cfg: Config, terms, *, full: bool = False, limit: int = 20) -> tuple[list[Hit], int]:
    """Rank cards by BM25 over L0/L1 (optionally L2). Length normalization stops a long card from
    out-ranking a short, on-point one by sheer repetition; IDF weights a rare, discriminating term
    above a common one. Substring term-frequency is kept (so `narrat` still finds `narrative`) —
    BM25 changes the *weighting*, not what matches.

    Returns `(top_hits, total_matched)` so a caller can report what it truncated.
    """
    needles = [t.lower() for t in terms if t.strip()]
    if not needles:
        return [], 0
    # pass 1 — gather each card's searchable blob + the metadata a Hit needs
    docs = []
    for p in card_files(cfg):
        text = read(p)
        fm = front_matter(text)
        sec = sections(text)
        blob = " ".join([
            os.path.basename(p)[:-3], title(text), fm_get(fm, "aliases") or "",
            sec.get("thin", ""), sec.get("full", "") if full else "",
        ]).lower()
        docs.append((os.path.basename(p)[:-3], blob, fm, sec))
    n = len(docs)
    if not n:
        return [], 0
    lengths = [max(1, len(blob.split())) for _, blob, _, _ in docs]
    avgdl = sum(lengths) / n
    # df via substring presence, matching how tf is counted; +0.5 smoothing + log(1+·) keeps IDF ≥ 0
    df = {t: sum(1 for _, blob, _, _ in docs if t in blob) for t in needles}
    idf = {t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in needles}

    # pass 2 — BM25 score
    hits: list[Hit] = []
    for (stem, blob, fm, sec), dl in zip(docs, lengths):
        score = 0.0
        for t in needles:
            tf = blob.count(t)
            if not tf:
                continue
            score += idf[t] * (tf * (_BM25_K1 + 1)) / (tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * dl / avgdl))
        if score <= 0:
            continue
        thin_lines = sec.get("thin", "").splitlines()
        gist = next((l for l in thin_lines if l.strip() and not l.startswith("_(")), "").strip()
        hits.append(Hit(id=stem, score=score,
                        zoom=fm_get(fm, "zoom") or "stub",
                        shelf=fm_get(fm, "shelf") or "?",
                        gist=gist))
    hits.sort(key=lambda h: (-h.score, h.id))
    return hits[:limit], len(hits)


# ---- zoom (one level of one card) -----------------------------------------
def meta(cfg: Config, card_id: str) -> str | None:
    """L0 — the card's raw front-matter block, or None if the card is missing."""
    p = card_path(cfg, card_id)
    return front_matter(read(p)) if p else None


def body(cfg: Config, card_id: str, level: str) -> str | None:
    """L1/L2 — the `## Thin` or `## Full` body, or None if card/section is missing."""
    p = card_path(cfg, card_id)
    if not p:
        return None
    return sections(read(p)).get(level) or None


def card_title(cfg: Config, card_id: str) -> str:
    p = card_path(cfg, card_id)
    return title(read(p)) if p else ""


@dataclass(frozen=True)
class Source:
    rel: str | None          # path as recorded in the card's `file:` front-matter
    path: Path | None        # absolute path, only when it exists on this machine
    size: int | None

    @property
    def installed(self) -> bool:
        return self.path is not None


def source_of(cfg: Config, card_id: str) -> Source | None:
    """L3 — locate the card's source .txt. The corpus is git-ignored, so an absent
    file means 'not installed here', not 'missing'."""
    p = card_path(cfg, card_id)
    if not p:
        return None
    rel = fm_get(front_matter(read(p)), "file")
    if not rel:
        return Source(rel=None, path=None, size=None)
    # `file:` is card-authored: an absolute or `../` value would resolve outside the library tree,
    # and verify_quote/zoom-content read this path. Require containment within cfg.root — an
    # out-of-tree source is treated as 'not installed' (path=None), not read.
    try:
        abspath = (cfg.root / rel).resolve()
        if not abspath.is_relative_to(cfg.root.resolve()):
            return Source(rel=rel, path=None, size=None)
    except (OSError, ValueError):
        return Source(rel=rel, path=None, size=None)
    if not abspath.exists():
        return Source(rel=rel, path=None, size=None)
    return Source(rel=rel, path=abspath, size=abspath.stat().st_size)


# ---- lenses: the frameworks / syntheses layer ------------------------------
# A source card says what ONE book says. A *framework card* distills one thinker into
# a fixed 8-section shape, and a *synthesis* adjudicates a whole craft dimension across
# many thinkers, keeping their disagreements rather than averaging them. For someone
# applying the library (rather than researching it) this layer — not the source cards —
# is the product. Section numbers are stable across every framework card, so key on the
# number and not the prose heading, which varies ("Best practices (what to DO)" vs
# "(what a story engineer DOES)").
_FW_SECTION_RE = re.compile(r"^###\s*(\d+)\.\s*(.+?)\s*\n(.*?)(?=^###\s|\Z)", re.M | re.S)
_FW_SECTION_KEYS = {
    1: "engine", 2: "primitives", 3: "mechanism", 4: "practices",
    5: "boundary", 6: "stance", 7: "disagreement", 8: "on_dimension",
}
# NOT anchored to line-start: some cards put the journal citation before the field
# (`*Vision Research 49* · **Dimension:** Attention · **Source:** …`). Stop at the
# separator or the next bold field, whichever comes first.
_DIM_RE = re.compile(r"\*\*Dimension:\*\*\s*(.+?)\s*(?:·|\*\*|$)", re.M)
_ALIASES_RE = re.compile(r"^\*\*Aliases:\*\*\s*(.+)$", re.M)
_COREQ_RE = re.compile(r"^\*\*Core question:\*\*\s*(.+)$", re.M)
_SKIP = {"README", "GATE-TRIAGE"}


@dataclass(frozen=True)
class Lens:
    name: str
    kind: str              # "dimension" (a synthesis) or "framework" (one thinker)
    dimension: str         # which craft dimension it answers at
    status: str            # syntheses carry a tier (`proposed`/`canonical`/…); "" for frameworks
    summary: str


def _md_files(d: Path | None) -> list[Path]:
    if not d or not d.is_dir():
        return []
    # Skip non-panel files: `_`-prefixed meta/registry docs (_support-layers,
    # _description-toolkit, _TAXONOMY-…), gate ledgers (DELON-GATE, GATE-TRIAGE),
    # and README — none are dimensions or framework cards.
    return [
        p for p in sorted(d.glob("*.md"))
        if not p.stem.startswith("_") and "GATE" not in p.stem and p.stem not in _SKIP
    ]


def lenses(cfg: Config) -> tuple[list[Lens], list[Lens]]:
    """`(dimensions, frameworks)` — the routing table from a craft problem to a lens."""
    dims: list[Lens] = []
    for p in _md_files(cfg.syntheses):
        text = read(p)
        fm = front_matter(text)
        q = _COREQ_RE.search(text)
        dims.append(Lens(name=p.stem, kind="dimension",
                         dimension=fm_get(fm, "dimension") or p.stem,
                         status=fm_get(fm, "status") or "unknown",
                         summary=(q.group(1).strip() if q else "")))
    fws: list[Lens] = []
    for p in _md_files(cfg.frameworks):
        text = read(p)
        m = _DIM_RE.search(text)
        secs = framework_sections(text)
        first = (secs.get("engine", "").strip().split("\n")[0])[:160]
        fws.append(Lens(name=p.stem, kind="framework",
                        dimension=(m.group(1).strip() if m else "?"),
                        status="", summary=first))
    return dims, fws


def framework_sections(text: str) -> dict[str, str]:
    """The 8 canonical sections of a framework card, keyed by stable slug."""
    out: dict[str, str] = {}
    for m in _FW_SECTION_RE.finditer(text):
        key = _FW_SECTION_KEYS.get(int(m.group(1)))
        if key:
            out[key] = m.group(3).strip()
    return out


# A lens name is a bare filename stem. Block only path SEPARATORS (so `consult ../../etc/x` can't
# escape the layer to read an arbitrary `.md`) — but allow unicode, so an accented author/book stem
# (`garcía-márquez.md`, `lévi-strauss.md`) is still reachable. The trailing `.md` means even a bare
# `..` stays inside the dir (`"..md"`), so a separator is the only traversal vector.
_SAFE_STEM = re.compile(r"[^/\\]+")


def framework(cfg: Config, name: str) -> dict | None:
    """One thinker's distilled framework: engine, primitives, mechanism, what to DO…"""
    if not cfg.frameworks or not _SAFE_STEM.fullmatch(name or ""):
        return None
    p = cfg.frameworks / f"{name}.md"
    if not p.exists():
        return None
    text = read(p)
    m, a = _DIM_RE.search(text), _ALIASES_RE.search(text)
    return {
        "name": name,
        "title": title(text),
        "dimension": m.group(1).strip() if m else "?",
        "aliases": a.group(1).strip() if a else "",
        "sections": framework_sections(text),
    }


def dimension(cfg: Config, name: str) -> dict | None:
    """A whole craft dimension adjudicated across frameworks — disagreements intact."""
    if not cfg.syntheses or not _SAFE_STEM.fullmatch(name or ""):
        return None
    p = cfg.syntheses / f"{name}.md"
    if not p.exists():
        return None
    text = read(p)
    fm = front_matter(text)
    q = _COREQ_RE.search(text)
    if text.startswith("---"):
        _before, sep, rest = text[3:].partition("---")   # tolerate a missing closing fence
        body = rest.strip() if sep else text.strip()
    else:
        body = text
    return {
        "name": name,
        "status": fm_get(fm, "status") or "unknown",
        "cards": fm_get(fm, "cards") or "",
        "core_question": q.group(1).strip() if q else "",
        "body": body,
    }


@dataclass(frozen=True)
class LensMatch:
    kind: str          # "dimension" | "framework" | "source"
    name: str          # stem / card id
    title: str


# Drop noise words so "the rhetoric of fiction" resolves on rhetoric+fiction, not on the/of.
_CONSULT_STOP = {"the", "of", "a", "an", "and", "to", "in", "on", "for", "by", "&"}


def _name_tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower())
            if len(w) >= 2 and w not in _CONSULT_STOP]


def resolve_consult(cfg: Config, text: str) -> tuple[str, list[LensMatch]]:
    """Resolve a free-text name — a stem, an author, or a book title — to a lens.
    Returns (status, matches): 'exact' (stem hit) | 'unique' | 'ambiguous' | 'none' (matches then
    holds suggestions). A framework represents its book; only source-only books match as sources."""
    stem = text.strip().lower().replace(" ", "-")
    if _SAFE_STEM.fullmatch(stem):                                  # exact stem, lens-first
        if dimension(cfg, stem):
            return ("exact", [LensMatch("dimension", stem, "")])
        fw = framework(cfg, stem)
        if fw:
            return ("exact", [LensMatch("framework", stem, fw["title"])])
        sp = cfg.cards / f"{stem}.md"
        if sp.exists():
            return ("exact", [LensMatch("source", stem, title(read(sp)))])
        # dimension-family fallback: a query that NAMES a family ("viewpoint") but is not an
        # exact stem ("viewpoint-who") must reach the family — never be won by an incidental
        # framework alias in the token scoring below (parity defect §1).
        fam = sorted(p.stem for p in _md_files(cfg.syntheses) if p.stem.startswith(stem + "-"))
        if len(fam) == 1:
            return ("exact", [LensMatch("dimension", fam[0], "")])
        if len(fam) >= 2:
            return ("ambiguous", [LensMatch("dimension", n, "") for n in fam])
    toks = _name_tokens(text)
    if not toks:
        return ("none", [])

    from .build import framework_source_map                        # lazy: avoids an import cycle
    fmap = framework_source_map(cfg)                               # source-rel -> framework-rel
    src_of_fw = {os.path.basename(v)[:-3]: os.path.basename(k)[:-4] for k, v in fmap.items()}
    have_fw = set(src_of_fw.values())

    def _src_extra(fw_stem: str) -> str:
        sp = cfg.cards / f"{src_of_fw.get(fw_stem, '')}.md"        # fold the book's own title/aliases in
        if not src_of_fw.get(fw_stem) or not sp.exists():
            return ""
        t = read(sp)
        return f"{title(t)} {fm_get(front_matter(t), 'aliases') or ''}"

    index: list[tuple[str, str, str, str]] = []                    # (kind, name, title, hay)
    for p in _md_files(cfg.frameworks):
        t = read(p)
        a = _ALIASES_RE.search(t)
        ti = title(t)
        hay = " ".join([p.stem, ti, a.group(1) if a else "", _src_extra(p.stem)]).lower()
        index.append(("framework", p.stem, ti, hay))
    for p in card_files(cfg):
        cid = os.path.basename(p)[:-3]
        if cid in have_fw:                                         # represented by its framework
            continue
        t = read(p)
        ti = title(t)
        hay = " ".join([cid, ti, fm_get(front_matter(t), "aliases") or ""]).lower()
        index.append(("source", cid, ti, hay))

    def score(hay: str) -> int:
        return sum(bool(re.search(rf"\b{re.escape(tk)}\b", hay)) for tk in toks)

    ranked = sorted(((score(hay), kind, name, ti) for kind, name, ti, hay in index),
                    key=lambda x: (-x[0], x[2]))
    full = [LensMatch(k, n, ti) for sc, k, n, ti in ranked if sc == len(toks)]
    if len(full) == 1:
        # a lone framework hit is not confident enough if the query is also a substring of
        # >=2 dimension stems — surface the dimensions rather than answer (parity defect §1c).
        dim_hits = sorted(p.stem for p in _md_files(cfg.syntheses) if stem in p.stem)
        if len(dim_hits) >= 2:
            return ("ambiguous", [LensMatch("dimension", n, "") for n in dim_hits] + full)
        return ("unique", full)
    if len(full) > 1:
        return ("ambiguous", full[:8])
    return ("none", [LensMatch(k, n, ti) for sc, k, n, ti in ranked if sc > 0][:5])


@dataclass(frozen=True)
class Resolution:
    """Single source of truth for what a consult query resolved to AND the message to show when it
    did not land on one lens. Both frontends (CLI `consult`, MCP `consult_*`) consume this, so the
    status->response policy lives in one place, not duplicated in two renderers (parity defect §6)."""
    outcome: str                        # "dimension" | "framework" | "source" | "ambiguous" | "none"
    match: LensMatch | None             # set iff outcome in {dimension, framework, source}
    candidates: tuple[LensMatch, ...]   # set iff outcome in {ambiguous, none}
    note: str                           # canonical user-facing line for ambiguous/none ("" otherwise)


def resolve(cfg: Config, text: str) -> Resolution:
    """resolve_consult + the shared status->response policy, as one object."""
    status, matches = resolve_consult(cfg, text)
    q = text.strip()
    if status in ("exact", "unique") and matches:
        return Resolution(matches[0].kind, matches[0], (), "")
    if status == "ambiguous":
        note = (f"{q!r} matches several lenses — name one:\n"
                + "\n".join(f"  - {m.kind}: {m.name}" + (f" — {m.title}" if m.title else "")
                            for m in matches))
        return Resolution("ambiguous", None, tuple(matches), note)
    hint = (f" Did you mean: {', '.join(m.name for m in matches)}?" if matches
            else f"  ·  browse: `klode consult`   ·   search: `klode search {q}`")
    return Resolution("none", None, tuple(matches), f"no lens for {q!r}.{hint}")


# ---- diagnose: symptom -> dimensions -----------------------------------------
def _cue_hit(cue: str, symptom: str) -> bool:
    r"""Match one diagnostic cue against the symptom. ASCII cues use word boundaries (so `pace`
    does not fire inside `spacecraft`); a cue with no ASCII word chars (a CJK phrase) has no
    meaningful `\b` between two CJK characters, so it matches as a substring — otherwise it would
    be silently inert inside a real sentence in that language (parity defect §4)."""
    if re.search(r"[A-Za-z0-9]", cue):
        return bool(re.search(rf"\b{re.escape(cue)}\b", symptom))
    return cue in symptom


def _load_diagnostics(cfg: Config) -> list[tuple[list[str], list[str]]] | None:
    """Parse `_syntheses/_diagnostics.md` — a hand-editable `| symptom cues | dimensions |` table.
    Library-owned data, not baked into the tool. (Moved here from the CLI so MCP can route too.)"""
    if not cfg.syntheses:
        return None
    p = cfg.syntheses / "_diagnostics.md"
    if not p.exists():
        return None
    rows: list[tuple[list[str], list[str]]] = []
    seen_sep = False                                    # the `|---|---|` row splits header from body
    for line in read(p).splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line.strip("|"))]
        if cells and all(c and set(c) <= set("-:") for c in cells):
            seen_sep = True
            continue
        if not seen_sep or len(cells) < 2:              # any row before the separator is a header
            continue
        cues = [c.strip().lower() for c in cells[0].split(",") if c.strip()]
        dims = [d.strip() for d in cells[1].split(",") if d.strip()]
        if cues and dims:
            rows.append((cues, dims))
    return rows


def diagnose(cfg: Config, symptom: str) -> list[tuple[str, str]] | None:
    """Route a 'how the draft feels wrong' phrase to the dimensions worth consulting.
    -> [(dimension_stem, core_question)] most-relevant-first; [] if nothing matched; None if the
    library has no diagnostics map. Shared by CLI `diagnose` and MCP `diagnose` (parity defect §3)."""
    rows = _load_diagnostics(cfg)
    if rows is None:
        return None
    sym = symptom.lower()
    scored = sorted(((sum(_cue_hit(c, sym) for c in cues), dims) for cues, dims in rows),
                    key=lambda x: -x[0])
    ordered: list[str] = []                             # dedupe, most-relevant-first, normalized
    for score, dims in scored:
        if score <= 0:
            continue
        for dnm in dims:
            dn = dnm.strip().lower().replace(" ", "-")
            if dn not in ordered:
                ordered.append(dn)
    return [(dn, (dimension(cfg, dn) or {}).get("core_question", "") or "") for dn in ordered]


def gate_verdict(cfg: Config, name: str) -> str | None:
    """The skeptic gate's row for a dimension, when one has been staged for review."""
    if not cfg.syntheses:
        return None
    p = cfg.syntheses / "GATE-TRIAGE.md"
    if not p.exists():
        return None
    for line in read(p).splitlines():
        if line.startswith("|") and f"**{name.replace('-', '/').title()}" in line.replace("Gap/Surprise", "Gap/surprise"):
            return line.strip()
    for line in read(p).splitlines():          # fall back to a looser name match
        if line.startswith("|") and name.split("-")[0].lower() in line.lower():
            return line.strip()
    return None


@dataclass(frozen=True)
class Verification:
    found: bool
    rel: str
    lines: list[tuple[int, str]]   # (1-indexed line number, stripped text)
    folded_only: bool              # resolved only after whitespace/hyphenation folding


def verify(cfg: Config, card_id: str, phrase: str, *, max_lines: int = 10) -> Verification | None:
    """Check a phrase against the card's source — the same matcher `klode check` uses.

    `folded_only` means the phrase resolves across line/hyphenation folding but does
    not appear on any single raw line, so there is nothing literal to quote back.
    """
    src = source_of(cfg, card_id)
    if src is None or not src.installed:
        return None
    raw = src.path.read_text(encoding="utf-8", errors="replace")
    if not occurs(phrase, haystacks(raw)):
        return Verification(found=False, rel=src.rel, lines=[], folded_only=False)
    pat = re.compile(re.escape(phrase), re.I)
    lines: list[tuple[int, str]] = []
    for i, ln in enumerate(raw.splitlines(), 1):
        if pat.search(ln):
            lines.append((i, ln.strip()))
            if len(lines) >= max_lines:
                break
    return Verification(found=True, rel=src.rel, lines=lines, folded_only=not lines)
