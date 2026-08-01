"""loopb — a supervising agent (Loop B) built as a thin consumer of klode (Loop A).

It never touches klode internals: it imports the `klode` facade and grounds every cited defect
through `lib.verify`. See README.md.
"""
from .criteria import Criterion, Grounding, ground, load_criteria
from .judge import FixtureJudge, Judge, Score
from .review import Line, Verdict, review_draft

__all__ = [
    "Criterion", "Grounding", "ground", "load_criteria",
    "Judge", "FixtureJudge", "Score",
    "Verdict", "Line", "review_draft",
]
