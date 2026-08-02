"""`review_draft` — the supervising verb. The one thing klode itself deliberately does NOT have:
submit a draft, score it against grounded criteria, and return a Cooper-style verdict whose every
cited defect is verifiable against the source.

Verdict logic (fail-CLOSED): the rubric only scores when EVERY criterion grounds. If any criterion
lacks current, unambiguous evidence, the verdict is "Unavailable" (no score) — a criterion is never
dropped-and-renormalized, since that could turn a Recycle into a Go. When all ground, score each
0-10: percentage >= hurdle -> Go, else Recycle ("send back with these specific, grounded defects").
"""
from __future__ import annotations

from dataclasses import dataclass

from . import criteria as _crit


@dataclass(frozen=True)
class Line:
    criterion: _crit.Criterion
    score: int
    note: str
    grounding: _crit.Grounding


@dataclass(frozen=True)
class Verdict:
    decision: str                       # "Go" | "Recycle" | "Unavailable"
    score: int | None                   # 0-100, or None when Unavailable (no valid verdict)
    hurdle: int
    lines: tuple[Line, ...]             # grounded criteria, scored (empty when Unavailable)
    unavailable: tuple[tuple[str, str], ...] = ()   # (criterion_id, reason) that blocked a verdict

    @property
    def defects(self) -> tuple[Line, ...]:
        """The Recycle reasons: criteria scoring below the bar (`score*10 < hurdle`), each with a
        grounded, un-fakeable citation. The threshold tracks the verdict policy, not a magic 6."""
        return tuple(sorted((l for l in self.lines if l.score * 10 < self.hurdle), key=lambda l: l.score))

    @property
    def ungrounded(self) -> tuple[str, ...]:
        """Deprecated compat shim for the pre-fail-closed field: the ids that blocked the verdict.
        Prefer `unavailable`, which also carries each id's reason."""
        return tuple(cid for cid, _ in self.unavailable)


def review_draft(cfg, draft: str, dimension: str, judge, *, hurdle: int = 60,
                 require_stamp: bool = False, today=None) -> Verdict:
    """Fail-CLOSED: EVERY criterion must have current, unambiguous evidence or the verdict is
    "Unavailable" — never Go/Recycle. Dropping an ungrounded criterion and renormalizing the average
    (the old behavior) could turn a Recycle into a Go, so a gate that loses evidence must abstain,
    not pass. Only when the whole rubric grounds does the judge score and a threshold verdict issue."""
    if not 0 <= hurdle <= 100:
        raise ValueError(f"hurdle must be in 0..100, got {hurdle}")
    crit, panel = _crit.load_criteria(cfg, dimension)
    grounded: list[tuple[_crit.Criterion, _crit.Grounding]] = []
    unavailable: list[tuple[str, str]] = []
    for c in crit:
        g = _crit.ground(cfg, c, panel, require_stamp=require_stamp, today=today)
        if g.grounded:
            grounded.append((c, g))
        else:
            unavailable.append((c.id, g.resolution or "not-grounded"))
    if unavailable:                                     # any missing/stale/ambiguous evidence -> no verdict
        return Verdict("Unavailable", None, hurdle, (), tuple(unavailable))
    by_id: dict = {}
    for s in judge.score(draft, [c for c, _ in grounded]):
        if s.criterion_id in by_id:
            raise ValueError(f"judge returned a duplicate score for {s.criterion_id!r}")
        if not 0 <= s.score <= 10:
            raise ValueError(f"judge score for {s.criterion_id!r} out of range 0..10: {s.score}")
        by_id[s.criterion_id] = s
    missing = {c.id for c, _ in grounded} - by_id.keys()
    if missing:
        raise ValueError(f"judge did not score grounded criteria: {sorted(missing)}")
    lines = tuple(Line(c, by_id[c.id].score, by_id[c.id].note, g) for c, g in grounded)
    total = round(100 * sum(l.score for l in lines) / (10 * len(lines)))
    return Verdict("Go" if total >= hurdle else "Recycle", total, hurdle, lines, ())
