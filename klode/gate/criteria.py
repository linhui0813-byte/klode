"""Load a craft dimension's Craft-layer moves as gate criteria, and GROUND each one through
`lib.verify` — the un-fakeable-citation step that a plain-RAG reviewer cannot do.

A criterion is trustworthy only if its cited source phrase actually resolves in a panel source. That
check is klode's, not ours: we call the same literal-grep verifier the citation-rot linter uses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from klode import lib   # the public facade — this package never reaches into klode internals


@dataclass(frozen=True)
class Criterion:
    id: str
    statement: str                 # the move / what a draft should do
    phrases: tuple[str, ...]       # verbatim source phrases backing it (the anchors)


@dataclass(frozen=True)
class Grounding:
    grounded: bool
    phrase: str | None = None      # the phrase that resolved
    card: str | None = None        # the source card it resolved in
    line: int | None = None        # 1-indexed line


_MOVE_HEAD = re.compile(r"^- \*\*(.+?)\*\*")            # the bold move name at a bullet's head
_GREP_RE = re.compile(r"grep:\s*`([^`]+)`")
_PANEL_RE = re.compile(r"[\[\]]")
_ANNOT_RE = re.compile(r"\s*\([^)]*\)")                 # a card annotation, e.g. `sternberg (cross-ref)`
_BULLET_SPLIT = re.compile(r"\n(?=- \*\*)")            # split the Craft moves into whole bullets


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
        head = _MOVE_HEAD.match(block.lstrip("\n"))
        if not head:
            continue
        phrases = tuple(_GREP_RE.findall(block))
        if phrases:                                     # a move with no anchor is not a gate criterion
            crit.append(Criterion(f"C{len(crit) + 1}", head.group(1).strip().rstrip("."), phrases))
    if not crit:
        raise ValueError(f"{dimension!r} Craft layer has no anchored moves — the gate cannot operate")
    return crit, panel


def ground(cfg, criterion: Criterion, panel: list[str]) -> Grounding:
    """Grounded only if EVERY cited phrase resolves in a panel source (lib.verify decides). A
    criterion mixing one real and three fabricated citations is NOT grounded — that is the point."""
    first: tuple | None = None
    for phrase in criterion.phrases:
        hit = None
        for card in panel:
            v = lib.verify(cfg, card, phrase)
            if v and v.found:
                hit = (phrase, card, v.lines[0][0] if v.lines else None)
                break
        if hit is None:
            return Grounding(False, phrase=phrase)      # this cited phrase resolves in no panel source
        if first is None:
            first = hit
    return Grounding(True, *first) if first else Grounding(False)
