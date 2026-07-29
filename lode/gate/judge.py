"""The rubric judge — scores a draft against each criterion (G-Eval-style: a numeric score per
dimension). Kept behind an interface so the WALKING SKELETON's chain (load → ground → verdict) is
testable without a model. The real LLM judge (two-step form-filling, position-bias-debiased,
different-model-than-author, calibrated against a human gold set) is a drop-in `Judge` — same
`score()` signature. It does NOT get to invent citations: grounding is enforced upstream by
`criteria.ground`, which is lodlib's literal-grep verifier, not the judge's word.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Score:
    criterion_id: str
    score: int          # 0-10
    note: str


class Judge(Protocol):
    def score(self, draft: str, criteria) -> list[Score]: ...


class FixtureJudge:
    """Deterministic judge for tests + the demo. Maps criterion id -> (score, note)."""

    def __init__(self, scores: dict[str, tuple[int, str]], default: tuple[int, str] = (7, "acceptable")):
        self._scores = scores
        self._default = default

    def score(self, draft: str, criteria) -> list[Score]:
        return [Score(c.id, *self._scores.get(c.id, self._default)) for c in criteria]
