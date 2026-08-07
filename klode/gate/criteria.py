"""Load a craft dimension's Craft-layer moves as gate criteria, and GROUND each one through
`lib.verify_evidence` — the un-fakeable-citation step that a plain-RAG reviewer cannot do.

A criterion is trustworthy only if its cited source phrase resolves — freshly and unambiguously — in
a panel source. That check is klode's, not ours: the structured, freshness-aware grounding verifier.

Each move carries more than a label: the bold head is the imperative `statement`, and the prose that
follows it is the `guidance` a judge needs to score the move — it is captured, not discarded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from klode import lib   # the public facade — this package never reaches into klode internals


@dataclass(frozen=True)
class Criterion:
    id: str
    statement: str                 # the bold move — the imperative summary of what a draft should do
    phrases: tuple[str, ...]       # verbatim source phrases backing it (mirrors [m.phrase for m in markers])
    guidance: str = ""             # the move's prose — what it means / how to judge it (a judge needs this)
    criticality: str = "required"  # parsed from an optional [advisory] tag; NOT yet enforced — every
    #                                criterion currently gates the verdict (an advisory path is future work)
    markers: tuple = ()            # the full structured anchors (lib.Marker) — regex/context/#n honoured
    #                                at grounding; a Criterion built with only `phrases` grounds them bare

    def __post_init__(self):
        # markers and phrases must not diverge: a fabricated phrase paired with a real marker would
        # otherwise be silently omitted from grounding (only markers are grounded when present).
        if self.markers:
            if any(not isinstance(m, lib.Marker) for m in self.markers):
                raise ValueError("Criterion.markers must all be lib.Marker instances")
            if tuple(m.phrase for m in self.markers) != tuple(self.phrases):
                raise ValueError("Criterion.phrases must mirror markers (phrase-per-marker, in order)")


@dataclass(frozen=True)
class Grounding:
    grounded: bool
    phrase: str | None = None      # the FIRST anchor's phrase (grounded) or the failing phrase (ungrounded)
    card: str | None = None        # the first anchor's source card
    line: int | None = None        # the first anchor's 1-indexed line
    resolution: str | None = None  # when NOT grounded: the EvidenceResolution value (the reason why)
    anchors: tuple = ()            # EVERY resolved anchor: (lib.Marker, card, line) — the full selector,
    #                                not just the phrase, so downstream re-verification is faithful


_MOVE_HEAD = re.compile(r"^- \*\*(.+?)\*\*")            # the bold move name at a bullet's head
# a whole (grep:/grep-re:/search:/search-re: …) marker — backtick-aware, so a `)` INSIDE the quoted
# phrase does not close it early, and every anchor key is stripped from the guidance/advisory text
_GREP_MARKER = re.compile(r"\(\s*(?:grep|search)(?:-re)?:(?:[^`)]|`[^`]*`)*\)", re.S)
_ADVISORY = re.compile(r"\[advisory\]", re.I)          # an optional criticality tag on a move
_PANEL_RE = re.compile(r"[\[\]]")
_ANNOT_RE = re.compile(r"\s*\([^)]*\)")                 # a card annotation, e.g. `sternberg (cross-ref)`
_BULLET_SPLIT = re.compile(r"\n(?=- \*\*)")            # split the Craft moves into whole bullets

# When a phrase grounds in NO panel card, report the most diagnostic reason — a freshness/review
# failure (the source is there but untrustworthy) outranks a plain not-found/ambiguous, so a stale
# source is never hidden behind a later card's not-found.
_REASON_RANK = {
    lib.EvidenceResolution.SOURCE_STALE.value: 5,
    lib.EvidenceResolution.REVIEW_EXPIRED.value: 5,
    lib.EvidenceResolution.REVIEW_DATE_INVALID.value: 5,
    lib.EvidenceResolution.SOURCE_UNSTAMPED.value: 4,
    lib.EvidenceResolution.AMBIGUOUS.value: 3,
    lib.EvidenceResolution.SOURCE_NOT_INSTALLED.value: 2,
    lib.EvidenceResolution.NOT_FOUND.value: 1,
}


def _panel(cards: str) -> list[str]:
    """The dimension's source-card stems, with any `(annotation)` stripped so verify_evidence gets real ids."""
    out: list[str] = []
    for part in _PANEL_RE.sub("", cards).split(","):
        stem = _ANNOT_RE.sub("", part).strip()
        if stem:
            out.append(stem)
    return out


def load_criteria(cfg, dimension: str) -> tuple[list[Criterion], list[str]]:
    """(criteria, panel) for a dimension — its Craft-layer moves and the source cards they cite."""
    res = lib.consult(cfg, lib.ConsultRequest(dimension, projection="writer"))
    if res.outcome != "dimension" or res.view is None or not res.selected:
        raise ValueError(f"{dimension!r} has no Craft layer to draw criteria from")
    craft = res.selected[0][1]
    panel = _panel(res.view.cards)                      # from the consult view — no second load, no reparse
    crit: list[Criterion] = []
    for block in _BULLET_SPLIT.split(craft):            # a whole bullet, incl. wrapped continuation lines
        b = block.lstrip("\n")
        head = _MOVE_HEAD.match(b)
        if not head:
            continue                                    # not a bold-headed move bullet — ignore
        markers = tuple(lib.parse_markers(b))          # the SHARED anchor parser (same as the linter)
        if not markers:
            # FAIL-CLOSED at the parser boundary: a stated move with no resolvable anchor must not be
            # silently dropped — that would let the gate score a reduced rubric and return Go.
            raise ValueError(f"{dimension!r} Craft move {head.group(1).strip()!r} has no (grep: `…`) "
                             "anchor — an unanchored move cannot become a gate criterion")
        after = b[head.end():]
        demarked = _GREP_MARKER.sub("", after)          # anchor markers removed (so [advisory]/prose below
        criticality = "advisory" if _ADVISORY.search(demarked) else "required"   # can't false-match inside one)
        guidance = " ".join(_ADVISORY.sub("", demarked).split()).strip(" .—-")
        crit.append(Criterion(f"C{len(crit) + 1}", head.group(1).strip().rstrip("."),
                              tuple(m.phrase for m in markers),
                              guidance=guidance, criticality=criticality, markers=markers))
    if not crit:
        raise ValueError(f"{dimension!r} Craft layer has no anchored moves — the gate cannot operate")
    return crit, panel


def ground_bindings(cfg, criterion: Criterion, bindings, *,
                    require_stamp: bool = False, today=None) -> Grounding:
    """Ground each anchor against the ONE card it was declared against.

    `ground()` searches the whole panel, which is right when the citation carries no card (a Craft
    move's bare `(grep: …)`). A CriterionSpec DOES name a card per anchor, and searching past it let
    a rotted citation migrate: a phrase filed to `structure` resolved in `brevity` and scored Go.
    Per-citation provenance means the declared card is the only place the quote may come from."""
    ok = (lib.EvidenceResolution.FOUND, lib.EvidenceResolution.FOLDED_ONLY)
    resolved: list[tuple] = []
    for marker, card in bindings:
        ev = lib.verify_evidence(cfg, card, marker, require_stamp=require_stamp, today=today)
        if ev.resolution not in ok:
            return Grounding(False, phrase=marker.phrase, resolution=ev.resolution.value)
        resolved.append((marker, card, ev.lines[0][0] if ev.lines else None))
    if not resolved:
        return Grounding(False)
    marker0, c, ln = resolved[0]
    return Grounding(True, phrase=marker0.phrase, card=c, line=ln, anchors=tuple(resolved))


def ground(cfg, criterion: Criterion, panel: list[str], *,
           require_stamp: bool = False, today=None) -> Grounding:
    """Grounded only if EVERY cited phrase resolves — freshly and in EXACTLY ONE panel source, as
    decided by `lib.verify_evidence` (NOT the occurrence-only `verify`). A criterion mixing one real
    and three fabricated citations is NOT grounded; nor is one whose source is stale, unstamped (when
    required), or past its review date; nor one whose phrase resolves in more than one panel card
    (ambiguous provenance — every panel card is checked, not just the first). The reason is recorded."""
    ok = (lib.EvidenceResolution.FOUND, lib.EvidenceResolution.FOLDED_ONLY)
    resolved: list[tuple] = []                                    # EVERY anchor's grounding, not just the first
    # full Markers when present (regex/context/#n honoured); a phrase-only Criterion grounds bare.
    markers = criterion.markers or tuple(lib.Marker(p) for p in criterion.phrases)
    for marker in markers:
        hits: list[tuple] = []
        reason = lib.EvidenceResolution.NOT_FOUND.value           # if the panel is empty, it is not found
        for card in panel:                                        # check EVERY card — don't stop at the first
            ev = lib.verify_evidence(cfg, card, marker, require_stamp=require_stamp, today=today)
            if ev.resolution in ok:
                hits.append((marker, card, ev.lines[0][0] if ev.lines else None))   # keep the MARKER, not the bare phrase
                continue
            cand = ev.resolution.value                            # keep the most diagnostic failure reason
            if _REASON_RANK.get(cand, 0) >= _REASON_RANK.get(reason, 0):
                reason = cand
        if len(hits) > 1:                                         # resolves in >1 card -> provenance is ambiguous
            return Grounding(False, phrase=marker.phrase, resolution="ambiguous-panel")
        if not hits:
            return Grounding(False, phrase=marker.phrase, resolution=reason)   # resolves in no panel source
        resolved.append(hits[0])
    if not resolved:
        return Grounding(False)
    marker0, c, ln = resolved[0]                                  # anchors hold (Marker, card, line)
    return Grounding(True, phrase=marker0.phrase, card=c, line=ln, anchors=tuple(resolved))
