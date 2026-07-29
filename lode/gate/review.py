"""`review_draft` — the supervising verb. The one thing lodlib itself deliberately does NOT have:
submit a draft, score it against grounded criteria, and return a Cooper-style verdict whose every
cited defect is verifiable against the source.

Verdict logic (Cooper's should-meet scorecard): score each grounded criterion 0-10; a criterion whose
citation cannot be verified is dropped and flagged (an ungrounded criterion is not trustworthy);
percentage >= hurdle -> Go, else Recycle ("send back with these specific, grounded defects").
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
    decision: str                       # "Go" | "Recycle"
    score: int                          # 0-100
    hurdle: int
    lines: tuple[Line, ...]             # grounded criteria, scored
    ungrounded: tuple[str, ...]         # criterion ids dropped for failing to ground (flagged, not silent)

    @property
    def defects(self) -> tuple[Line, ...]:
        """The Recycle reasons: criteria scoring below the bar (`score*10 < hurdle`), each with a
        grounded, un-fakeable citation. The threshold tracks the verdict policy, not a magic 6."""
        return tuple(sorted((l for l in self.lines if l.score * 10 < self.hurdle), key=lambda l: l.score))


def review_draft(cfg, draft: str, dimension: str, judge, *, hurdle: int = 60) -> Verdict:
    if not 0 <= hurdle <= 100:
        raise ValueError(f"hurdle must be in 0..100, got {hurdle}")
    crit, panel = _crit.load_criteria(cfg, dimension)
    grounded: list[tuple[_crit.Criterion, _crit.Grounding]] = []
    ungrounded: list[str] = []
    for c in crit:
        g = _crit.ground(cfg, c, panel)
        if g.grounded:
            grounded.append((c, g))
        else:
            ungrounded.append(c.id)
    if not grounded:                                    # no evidence to score against — do not fake a verdict
        raise ValueError(f"no criterion for {dimension!r} grounded in a source — the gate cannot operate")
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
    return Verdict("Go" if total >= hurdle else "Recycle", total, hurdle, lines, tuple(ungrounded))
