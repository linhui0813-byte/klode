"""The extraction-integrity decision: turn measurements into one of four states.

Four states, because "did not run" and "ran and failed" are different facts and collapsing them is
how a check becomes decorative:

| state | meaning |
|---|---|
| `verified` | measured, and every signal cleared its threshold |
| `failed` | measured, and at least one signal did not clear |
| `abstained` | could not measure (no control, too few anchors, tools absent) |
| `unverified` | measurable, but the operator chose to skip or accept — recorded, not hidden |

`abstained` must never be read as `verified`. That is the whole reason it exists.

**Thresholds here are provisional.** They are set to catch the demonstrated failure modes —
dropped pages, duplicated blocks, inverted reading order — and deliberately not tuned finer,
because tuning them needs a labeled corpus with real-world failures that does not exist yet (see
`tests/fixtures/pdfs/GROUND-TRUTH.json` `not_covered`). Every decision therefore carries its
numbers AND the thresholds that judged them, so a later recalibration can be applied to records
already written.

Pure: no I/O, no subprocess, no backend.
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["Thresholds", "Integrity", "decide", "SCHEMA"]

SCHEMA = "klode.extraction-integrity/v1"

VERIFIED = "verified"
FAILED = "failed"
ABSTAINED = "abstained"
UNVERIFIED = "unverified"


@dataclass(frozen=True)
class Thresholds:
    """Provisional. Loose on purpose: a false refusal blocks a legitimately hard PDF, and these
    have not been calibrated against real-world failures."""
    min_containment: float = 0.80     # >20% of the control's tokens absent from the candidate
    max_inflation: float = 1.50       # candidate is half again the control — duplication
    min_inflation: float = 0.67       # candidate is two-thirds of the control — loss
    min_median_order: float = 0.0     # median window ACTIVELY inverted, not merely noisy

    def as_dict(self) -> dict:
        return {"min_containment": self.min_containment, "max_inflation": self.max_inflation,
                "min_inflation": self.min_inflation, "min_median_order": self.min_median_order}


@dataclass(frozen=True)
class Integrity:
    state: str
    reasons: tuple[str, ...] = ()
    metrics: dict = field(default_factory=dict)
    thresholds: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True only for `verified`. `abstained` is NOT ok — it is 'unknown', and a caller that
        treats unknown as fine has reintroduced the silent acceptance this module exists to stop."""
        return self.state == VERIFIED

    @property
    def blocks_write(self) -> bool:
        """Only a measured failure refuses promotion. Abstention does not — refusing every PDF we
        cannot measure would make the tool unusable on exactly the documents it is needed for."""
        return self.state == FAILED


def decide(agreement=None, coverage=None, *, thresholds: Thresholds | None = None,
           accepted: bool = False) -> Integrity:
    """Combine the measurements into a state.

    `agreement=None` and `coverage=None` mean nothing could be measured -> `abstained`.
    `accepted=True` records an operator override as `unverified` — the numbers are still recorded,
    because an override that erases its own evidence is worse than no check.
    """
    th = thresholds or Thresholds()
    metrics: dict = {}
    reasons: list[str] = []

    if agreement is not None:
        median = agreement.quantile(0.50)
        metrics.update({
            "containment": round(agreement.containment, 4),
            "inflation": round(agreement.inflation, 4),
            "control_tokens": agreement.control_tokens,
            "candidate_tokens": agreement.candidate_tokens,
            "windows": len(agreement.windows),
            "windows_abstained": agreement.abstained_windows,
            "order_median": None if median is None else round(median, 4),
            "order_p05": None if agreement.quantile(0.05) is None else round(agreement.quantile(0.05), 4),
            "order_worst": (None if agreement.worst_window is None
                            else round(agreement.worst_window.order, 4)),
        })
        if agreement.containment < th.min_containment:
            reasons.append(f"containment {agreement.containment:.3f} < {th.min_containment} "
                           "— material present in the control is missing from the candidate")
        if agreement.inflation > th.max_inflation:
            reasons.append(f"inflation {agreement.inflation:.3f} > {th.max_inflation} "
                           "— the candidate repeats material")
        elif agreement.inflation < th.min_inflation:
            reasons.append(f"inflation {agreement.inflation:.3f} < {th.min_inflation} "
                           "— the candidate is substantially shorter than the control")
        if median is not None and median < th.min_median_order:
            reasons.append(f"median window order {median:.3f} < {th.min_median_order} "
                           "— reading order is inverted across most of the document")

    if coverage is not None:
        metrics.update({
            "declared_pages": coverage.declared,
            "control_pages": coverage.control_pages,
            "control_empty_pages": list(coverage.control_empty),
            "candidate_pages_known": coverage.candidate_known,
            "candidate_missing_pages": list(coverage.candidate_missing),
        })
        if coverage.candidate_known and coverage.candidate_missing:
            reasons.append(f"candidate omits declared page(s) {list(coverage.candidate_missing)}")

    if accepted:
        return Integrity(UNVERIFIED, tuple(reasons), metrics, th.as_dict())
    if agreement is None and coverage is None:
        return Integrity(ABSTAINED, ("nothing could be measured",), metrics, th.as_dict())
    if reasons:
        return Integrity(FAILED, tuple(reasons), metrics, th.as_dict())
    return Integrity(VERIFIED, (), metrics, th.as_dict())
