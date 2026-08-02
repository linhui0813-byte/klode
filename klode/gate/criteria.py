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
    phrases: tuple[str, ...]       # verbatim source phrases backing it (the anchors)
    guidance: str = ""             # the move's prose — what it means / how to judge it (a judge needs this)
    criticality: str = "required"  # parsed from an optional [advisory] tag; NOT yet enforced — every
    #                                criterion currently gates the verdict (an advisory path is future work)


@dataclass(frozen=True)
class Grounding:
    grounded: bool
    phrase: str | None = None      # the phrase that resolved (grounded) or failed (ungrounded)
    card: str | None = None        # the source card it resolved in
    line: int | None = None        # 1-indexed line
    resolution: str | None = None  # when NOT grounded: the EvidenceResolution value (the reason why)


_MOVE_HEAD = re.compile(r"^- \*\*(.+?)\*\*")            # the bold move name at a bullet's head
_GREP_RE = re.compile(r"grep:\s*`([^`]+)`")
# a whole (grep: …) marker — backtick-aware, so a `)` INSIDE the quoted phrase does not close it early
_GREP_MARKER = re.compile(r"\(\s*grep:(?:[^`)]|`[^`]*`)*\)", re.S)
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
        phrases = tuple(_GREP_RE.findall(b))
        if not phrases:
            # FAIL-CLOSED at the parser boundary: a stated move with no resolvable anchor must not be
            # silently dropped — that would let the gate score a reduced rubric and return Go.
            raise ValueError(f"{dimension!r} Craft move {head.group(1).strip()!r} has no (grep: `…`) "
                             "anchor — an unanchored move cannot become a gate criterion")
        after = b[head.end():]
        demarked = _GREP_MARKER.sub("", after)          # anchor markers removed (so [advisory]/prose below
        criticality = "advisory" if _ADVISORY.search(demarked) else "required"   # can't false-match inside one)
        guidance = " ".join(_ADVISORY.sub("", demarked).split()).strip(" .—-")
        crit.append(Criterion(f"C{len(crit) + 1}", head.group(1).strip().rstrip("."), phrases,
                              guidance=guidance, criticality=criticality))
    if not crit:
        raise ValueError(f"{dimension!r} Craft layer has no anchored moves — the gate cannot operate")
    return crit, panel


def ground(cfg, criterion: Criterion, panel: list[str], *,
           require_stamp: bool = False, today=None) -> Grounding:
    """Grounded only if EVERY cited phrase resolves — freshly and unambiguously — in a panel source,
    as decided by `lib.verify_evidence` (NOT the occurrence-only `verify`). A criterion mixing one
    real and three fabricated citations is NOT grounded; nor is one whose source is stale, unstamped
    (when required), or past its review date. The failure reason is recorded for the verdict."""
    ok = (lib.EvidenceResolution.FOUND, lib.EvidenceResolution.FOLDED_ONLY)
    first: tuple | None = None
    for phrase in criterion.phrases:
        hit = None
        reason = lib.EvidenceResolution.NOT_FOUND.value           # if the panel is empty, it is not found
        for card in panel:
            ev = lib.verify_evidence(cfg, card, phrase, require_stamp=require_stamp, today=today)
            if ev.resolution in ok:
                hit = (phrase, card, ev.lines[0][0] if ev.lines else None)
                break
            cand = ev.resolution.value                            # keep the most diagnostic failure reason
            if _REASON_RANK.get(cand, 0) >= _REASON_RANK.get(reason, 0):
                reason = cand
        if hit is None:
            return Grounding(False, phrase=phrase, resolution=reason)   # resolves in no panel source
        if first is None:
            first = hit
    return Grounding(True, *first) if first else Grounding(False)
