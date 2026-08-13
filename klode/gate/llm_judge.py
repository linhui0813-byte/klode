"""The real rubric judge — an LLM behind the `Judge` protocol, debiased and calibration-gated.

The stub (`FixtureJudge`) proved the chain; this is the piece that actually reads a draft. Three
things make it more than a wrapper around a prompt, and all three come from the architecture note
(`dev-docs/supervising-agent-architecture-2026-07-28.md` §2.3):

**Two-step form-filling (G-Eval).** Step 1 turns a criterion into explicit evaluation *steps*,
without seeing the draft — so the standard is fixed before the thing being judged is in view, and
the same steps apply to every draft. Step 2 fills a form: one integer naming a behavioral level,
plus a note. Steps are derived once per criterion and reused.

**Balanced permutation.** Each criterion is scored more than once with the level descriptors
presented in a different order, and the results averaged. Position bias is the most severe defect in
rubric-judge studies: the same draft scores differently depending on where a band sits in the list.
Averaging over reversed orders cancels the first-order effect — CONDITIONALLY, on the two orders
being presented equally often, which is why `permutations` must be 1 or even and an odd count is
refused. It does not cancel every bias, which is why the third thing exists.

**Calibration gating — the part that matters.** A judge that has not been measured against human
scores on THIS rubric cannot report an authoritative verdict. `calibrated_for()` answers for one
specific rubric digest, because rewording a level descriptor produces a different instrument and an
agreement number measured on the old one does not transfer. With no `Calibration`, the judge still
runs — you need it to run in order to calibrate it — but `Verdict.calibrated` is False and the
review service marks the result non-production. There is no flag to assert calibration; the only
way to set it is to supply a record of measured agreement.

**Different model than the author.** Self-enhancement bias means a model over-rates its own family's
output. This class does not pick a default model: `model` is required, so choosing one different
from whatever produced the draft is a decision the operator makes explicitly rather than inherits.

Stdlib only (`urllib` is imported lazily inside the default transport), and the transport is
injectable — every test here runs without a network.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .judge import Score

_JSON_OBJ = re.compile(r"\{.*\}", re.S)


class JudgeError(RuntimeError):
    """The judge could not produce a usable score. Never swallowed into a default — a gate that
    invents a score when the model failed is worse than one that abstains."""


# Bumped whenever STEPS_PROMPT or FORM_PROMPT changes. Those prompts ARE the instrument: reword
# one and the judge answers differently, so an agreement number measured through the old wording
# does not describe the new one. `tests/test_llm_judge.py` pins the prompts to this constant, so a
# prompt edit that forgets to bump it fails there rather than silently inheriting a calibration.
PROMPT_VERSION = "1"


@dataclass(frozen=True)
class Calibration:
    """Measured agreement between this judge and human raters, on ONE rubric, through ONE judge.

    Not a claim — a record. `rubric_digest` is `spec.rubric_identity(spec)` of the rubric the
    measurement was taken against; `agreement` is quadratic-weighted kappa against human scores,
    the same statistic `eval/rate.py` reports for two humans.

    The instrument is BOTH halves. This class pinned only the rubric, on the reasoning that
    rewording a level descriptor produces a different instrument — which is true, and equally true
    of the judge doing the reading. A record measured with one model at `permutations=2` returned
    `covers() -> True` for a different model running undebiased at `permutations=1`, and
    `calibrated` is the single bit the review service uses to drop `non_production`. So `model`,
    `permutations` and `prompt_version` are part of the identity now.

    They default to None so an existing record still constructs — and then fails closed, since None
    matches no live judge. A stored calibration that stops claiming coverage is the correct
    migration; one that keeps claiming it for an unmeasured instrument is the defect.
    """
    rubric_digest: str
    n: int                       # human-scored drafts the agreement was measured over
    agreement: float             # QWK vs human
    bar: float = 0.6
    min_n: int = 20
    measured_on: str = ""        # ISO date; provenance, not logic
    model: str | None = None            # the judge model the agreement was measured through
    permutations: int | None = None     # the debiasing policy in force during measurement
    prompt_version: str | None = None   # PROMPT_VERSION at measurement time

    def clears(self) -> bool:
        return self.n >= self.min_n and self.agreement >= self.bar

    def covers(self, rubric_digest: str, *, model: str | None = None,
               permutations: int | None = None) -> bool:
        """True only when this record was measured on THIS rubric, through THIS judge.

        A missing field on either side means "not measured for that", so it fails closed rather
        than matching by omission."""
        if not (self.clears() and rubric_digest == self.rubric_digest):
            return False
        if self.prompt_version != PROMPT_VERSION:
            return False
        return (self.model is not None and self.model == model
                and self.permutations is not None and self.permutations == permutations)


def anthropic_transport(model: str, *, api_key_env: str = "ANTHROPIC_API_KEY",
                        max_tokens: int = 1024, timeout: float = 120.0):
    """A minimal stdlib transport: `(prompt) -> text`. Imported lazily so `klode.gate` stays cheap
    and network-free unless a caller actually asks for this."""
    def call(prompt: str) -> str:
        import urllib.error
        import urllib.request
        key = os.environ.get(api_key_env)
        if not key:
            raise JudgeError(f"{api_key_env} is not set — the judge has no way to reach a model")
        body = json.dumps({"model": model, "max_tokens": max_tokens,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=body,
            headers={"content-type": "application/json", "x-api-key": key,
                     "anthropic-version": "2023-06-01"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = json.loads(r.read().decode())
        except OSError as e:
            # OSError, not just URLError: `urlopen` raises bare socket errors
            # (ConnectionResetError, timeout) that URLError does not cover, and an uncaught one
            # crashes the review with a traceback instead of the documented JudgeError.
            raise JudgeError(f"judge transport failed: {e}") from e
        try:
            return "".join(b.get("text", "") for b in payload["content"])
        except (KeyError, TypeError) as e:
            raise JudgeError(f"unexpected model response shape: {payload!r:.200}") from e
    return call


def _levels_block(item, *, reverse: bool) -> str:
    levels = list(item.levels)
    if reverse:
        levels = levels[::-1]
    return "\n".join(f"  {lv.score} = {lv.descriptor.value}" for lv in levels)


def _evidence_block(item) -> str:
    """The verified source spans behind this criterion. The judge reads evidence the grounding layer
    already proved; it never supplies its own, and cannot cite anything not in here."""
    out = []
    for g in getattr(item.context, "grounded", ()):
        lines = "\n".join(f"      {n}: {t}" for n, t in getattr(g, "lines", ()))
        out.append(f"    [{getattr(g, 'card', '?')}] {getattr(g, 'phrase', '')}\n{lines}")
    return "\n".join(out) or "    (no evidence spans)"


STEPS_PROMPT = """You are calibrating a rubric criterion before any draft is seen.

CRITERION: {statement}
GUIDANCE: {guidance}
BEHAVIORAL LEVELS (score = what that band looks like):
{levels}

SOURCE EVIDENCE the criterion rests on:
{evidence}

Write 3-6 numbered evaluation steps a careful reviewer would follow to place a draft on this scale.
Steps must be observable operations on the draft (what to look for, what to count, what to compare),
not restatements of the levels. Output ONLY the numbered steps, nothing else."""

FORM_PROMPT = """Score one draft against one rubric criterion.

CRITERION: {statement}
GUIDANCE: {guidance}

EVALUATION STEPS — follow these in order:
{steps}

BEHAVIORAL LEVELS — choose the ONE whose description the draft actually matches:
{levels}

SOURCE EVIDENCE the criterion rests on:
{evidence}

DRAFT:
<<<
{draft}
>>>

Match the draft to a level descriptor. Do not average, do not hedge toward the middle, and do not
reward or penalize anything the criterion does not mention. Output ONLY a JSON object:
{{"score": <integer {lo}..{hi}>, "note": "<one sentence naming what in the draft put it at that level>"}}"""


class LLMJudge:
    """A `Judge` that asks a model, twice per criterion, in opposed level orders."""

    # `1` (one forward pass, explicitly undebiased and cheap) or an EVEN count. `reverse=bool(i%2)`
    # splits an odd count unevenly — at 3 it is two forward runs and one reversed — so the mean
    # keeps a share of the position bias the averaging exists to cancel, at 1.5x the API cost. The
    # trap is that 3 looks more thorough than 2 and is strictly less debiased.
    VALID_PERMUTATIONS = (1,) + tuple(range(2, 17, 2))

    def __init__(self, transport, *, model: str = "", permutations: int = 2,
                 calibration: "Calibration | None" = None):
        # `permutations < 1` was the whole guard, and `True < 1` is False — so `permutations=True`
        # constructed and silently ran ONE forward pass, and `2.0` got as far as `range()` before
        # failing with a TypeError from somewhere unrelated-looking.
        if isinstance(permutations, bool) or not isinstance(permutations, int):
            raise ValueError(f"permutations must be an integer, got {permutations!r}")
        if permutations not in self.VALID_PERMUTATIONS:
            raise ValueError(
                f"permutations must be 1 (one forward pass, explicitly undebiased) or an even "
                f"number up to 16 — got {permutations}. An odd count above 1 presents one order "
                f"more often than the other, so it retains the position bias the average exists "
                f"to cancel while costing more than the even count below it.")
        if not model or not model.strip():
            # There is no default model ON PURPOSE (self-enhancement bias: the judge must differ
            # from whatever produced the draft), but "no default" is only meaningful if the empty
            # value is REFUSED. It was not: `LLMJudge(t)` constructed happily and would have sent
            # `"model": ""` to the API, failing later and confusingly.
            raise ValueError("LLMJudge requires an explicit `model` — pick one from a DIFFERENT "
                             "family than whatever produced the draft (self-enhancement bias); "
                             "there is deliberately no default")
        self._call = transport
        self.model = model
        self._permutations = permutations
        self.calibration = calibration
        self._steps: dict[str, str] = {}

    # -- calibration -------------------------------------------------------
    @property
    def calibrated(self) -> bool:
        return bool(self.calibration and self.calibration.clears())

    def calibrated_for(self, rubric_digest: str) -> bool:
        """True only when the calibration was measured on THIS rubric, through THIS judge —
        same model, same permutation policy, same prompt version — and cleared its bar."""
        return bool(self.calibration and self.calibration.covers(
            rubric_digest, model=self.model, permutations=self._permutations))

    # -- scoring -----------------------------------------------------------
    def steps_for(self, item) -> str:
        """Step 1, memoized: the criterion's evaluation steps, derived without the draft in view."""
        if item.id not in self._steps:
            out = self._call(STEPS_PROMPT.format(
                statement=item.statement, guidance=item.guidance or "(none)",
                levels=_levels_block(item, reverse=False), evidence=_evidence_block(item)))
            if not (out or "").strip():
                raise JudgeError(f"{item.id}: the model returned no evaluation steps")
            self._steps[item.id] = out.strip()
        return self._steps[item.id]

    def _one(self, draft: str, item, steps: str, *, reverse: bool) -> tuple[int, str]:
        hi = item.max_score
        raw = self._call(FORM_PROMPT.format(
            statement=item.statement, guidance=item.guidance or "(none)", steps=steps,
            levels=_levels_block(item, reverse=reverse), evidence=_evidence_block(item),
            draft=draft, lo=0, hi=hi))
        m = _JSON_OBJ.search(raw or "")
        if not m:
            raise JudgeError(f"{item.id}: model output contained no JSON object: {raw!r:.200}")
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise JudgeError(f"{item.id}: model output was not valid JSON — {e}") from e
        score = obj.get("score")
        if isinstance(score, bool) or not isinstance(score, int):
            raise JudgeError(f"{item.id}: `score` must be an integer naming a level, got {score!r}")
        if not 0 <= score <= hi:
            raise JudgeError(f"{item.id}: score {score} is outside this criterion's scale 0..{hi}")
        note = obj.get("note")
        return score, (note if isinstance(note, str) else "")

    def score(self, draft: str, criteria) -> list[Score]:
        out: list[Score] = []
        for item in criteria:
            steps = self.steps_for(item)
            runs = [self._one(draft, item, steps, reverse=bool(i % 2))
                    for i in range(self._permutations)]
            # average over opposed orders, half-up: position bias moves the score by band position,
            # and the mean over reversed presentations cancels its first-order component
            mean = Decimal(sum(s for s, _ in runs)) / Decimal(len(runs))
            final = int(mean.quantize(Decimal(1), rounding=ROUND_HALF_UP))
            spread = max(s for s, _ in runs) - min(s for s, _ in runs)
            note = runs[0][1]
            if spread:
                # a judge that answers differently depending on band order has not really decided;
                # surfacing the spread stops an averaged number from looking more settled than it is
                note = f"{note} [order-sensitive: runs {[s for s, _ in runs]}]"
            out.append(Score(item.id, final, note))
        return out
