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
    criticality: str = "required"  # "required" (gates the verdict) | "advisory" (feedback-only, future)


@dataclass(frozen=True)
class Grounding:
    grounded: bool
    phrase: str | None = None      # the phrase that resolved (grounded) or failed (ungrounded)
    card: str | None = None        # the source card it resolved in
    line: int | None = None        # 1-indexed line
    resolution: str | None = None  # when NOT grounded: the EvidenceResolution value (the reason why)


_MOVE_HEAD = re.compile(r"^- \*\*(.+?)\*\*")            # the bold move name at a bullet's head
_GREP_RE = re.compile(r"grep:\s*`([^`]+)`")
_GREP_MARKER = re.compile(r"\(\s*grep:.*?\)", re.S)     # a whole (grep: …) marker, to strip from guidance
_ADVISORY = re.compile(r"\[advisory\]", re.I)          # an optional criticality tag on a move
_PANEL_RE = re.compile(r"[\[\]]")
_ANNOT_RE = re.compile(r"\s*\([^)]*\)")                 # a card annotation, e.g. `sternberg (cross-ref)`
_BULLET_SPLIT = re.compile(r"\n(?=- \*\*)")            # split the Craft moves into whole bullets


def _guidance(bullet_after_head: str) -> str:
    """The move's human explanation: the bullet prose minus the anchor markers and the advisory tag."""
    txt = _ADVISORY.sub("", _GREP_MARKER.sub("", bullet_after_head))
    return " ".join(txt.split()).strip(" .—-")


def _panel(cards: str) -> list[str]:
    """The dimension's source-card stems, with any `(annotation)` stripped so verify() gets real ids."""
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
            continue
        phrases = tuple(_GREP_RE.findall(b))
        if phrases:                                     # a move with no anchor is not a gate criterion
            crit.append(Criterion(
                f"C{len(crit) + 1}", head.group(1).strip().rstrip("."), phrases,
                guidance=_guidance(b[head.end():]),
                criticality="advisory" if _ADVISORY.search(b) else "required"))
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
            reason = ev.resolution.value                          # remember why this card did not ground it
        if hit is None:
            return Grounding(False, phrase=phrase, resolution=reason)   # resolves in no panel source
        if first is None:
            first = hit
    return Grounding(True, *first) if first else Grounding(False)
