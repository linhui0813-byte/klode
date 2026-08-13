"""loopb — a supervising agent (Loop B) built as a thin consumer of klode (Loop A).

It never touches klode internals: it imports the `klode` facade and grounds every cited defect
through `lib.verify`. See README.md.
"""
from .criteria import Criterion, Grounding, ground, ground_bindings, load_criteria
from .judge import FixtureJudge, Judge, Score
from .llm_judge import Calibration, JudgeError, LLMJudge, anthropic_transport
from .review import GradingItem, Line, Verdict, review_draft
from .spec import CriterionSpec, Field, Level, SpecCriterion, SpecError
from .spec import load as load_spec, parse as parse_spec, validate as validate_spec
from .spec import canonical_digest, rubric_identity

__all__ = [
    # the rubric — the gate's sole input
    "CriterionSpec", "SpecCriterion", "Field", "Level", "SpecError",
    "load_spec", "parse_spec", "validate_spec", "rubric_identity", "canonical_digest",
    # the grounding engine (also the seed for authoring a rubric from the Craft layer)
    "Criterion", "Grounding", "ground", "ground_bindings", "load_criteria",
    "Judge", "FixtureJudge", "Score", "LLMJudge", "Calibration", "JudgeError",
    "anthropic_transport",
    "Verdict", "Line", "GradingItem", "review_draft",
]
