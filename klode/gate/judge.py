"""The rubric judge — scores a draft against each criterion (G-Eval-style: a numeric score per
dimension). Kept behind an interface so the WALKING SKELETON's chain (load → ground → verdict) is
testable without a model. The real LLM judge (two-step form-filling, position-bias-debiased,
different-model-than-author, calibrated against a human gold set) is a drop-in `Judge` — same
`score()` signature. It does NOT get to invent citations: grounding is enforced upstream by
`criteria.ground`, which is klode's literal-grep verifier, not the judge's word.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol


@dataclass(frozen=True)
class Score:
    criterion_id: str
    score: int          # 0-10
    note: str


class Judge(Protocol):
    def score(self, draft: str, criteria) -> list[Score]: ...


class FixtureJudge:
    """Deterministic judge for tests + the demo. Maps criterion id -> (score, note).

    `default_fraction` expresses the fallback as a PROPORTION of each criterion's own behavioral
    scale, quantized HALF-UP (0.9 -> 9 on a 0..10 rubric, 5 on a 0..5 one — `round()` would give 4
    on the latter, since 4.5 ties to even). Rubrics no longer share a fixed 0-10, so a
    test that means "score everything strongly" has to say it scale-independently or it breaks the
    moment a rubric declares six levels instead of eleven. An absolute `default` still works and is
    still range-checked — out of range fails loud rather than clamping, since a judge that silently
    fits its answer to the scale is exactly the calibration bug this whole layer exists to expose."""

    def __init__(self, scores: dict[str, tuple[int, str]], default: tuple[int, str] = (7, "acceptable"),
                 default_fraction: float | None = None, note: str = "fixture"):
        self._scores = scores
        self._default = default
        if default_fraction is not None and not 0.0 <= default_fraction <= 1.0:
            raise ValueError(f"default_fraction must be in 0.0..1.0, got {default_fraction}")
        self._fraction = default_fraction
        self._note = note

    def _fallback(self, item) -> tuple[int, str]:
        if self._fraction is None:
            return self._default
        # HALF-UP, not `round()`: banker's rounding made `default_fraction=0.9` on a 0..5 scale
        # return 4 (=80%), not the 5 the name implies — a silent 10-point drift in every test and
        # demo that asked for "score everything strongly".
        top = getattr(item, "max_score", 10)
        # multiply IN Decimal: `0.58 * 25` is 14.499999999999998 in binary float, so quantizing the
        # product after the fact rounds down from a value that was meant to be exactly 14.5
        exact = Decimal(str(self._fraction)) * Decimal(top)
        return int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP)), self._note

    def score(self, draft: str, criteria) -> list[Score]:
        return [Score(c.id, *self._scores.get(c.id, self._fallback(c))) for c in criteria]
