"""`review_draft` — the supervising verb. The one thing klode itself deliberately does NOT have:
submit a draft, score it against grounded criteria, and return a Cooper-style verdict whose every
cited defect is verifiable against the source.

Its rubric is a **CriterionSpec** (`spec.py`) and nothing else — an authored, corpus-pinned,
human-approved artifact with stable ids and behaviorally anchored levels. It no longer promotes a
synthesis's bold bullets into positional `C1`/`C2` criteria: that made the rubric an accident of
prose order, and made every human label collected against an id void as soon as a bullet moved.

Verdict logic (fail-CLOSED): the rubric only scores when EVERY criterion grounds. If any criterion
lacks current, unambiguous evidence, the verdict is "Unavailable" (no score) — a criterion is never
dropped-and-renormalized, since that could turn a Recycle into a Go. When all ground, the judge
scores each on ITS OWN 0..N behavioral scale; the mean percentage >= hurdle -> Go, else Recycle
("send back with these specific, grounded defects").
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from klode import lib   # the public facade — for the verified-context bundle handed to the judge

from . import criteria as _crit
from . import spec as _spec


@dataclass(frozen=True)
class Line:
    criterion: _crit.Criterion
    score: int
    note: str
    grounding: _crit.Grounding
    max_score: int = 10        # the criterion's own behavioral scale (levels 0..max), not a fixed 0-10

    @property
    def pct(self) -> int:
        # FLOOR, for the reason the verdict floors: `defects` classifies by an exact comparison,
        # so rounding here let a criterion DISPLAY at the hurdle while being listed as a defect
        # below it. At hurdle 67 a 2/3 criterion is exactly 66.67%, showed as 67, and was still
        # (correctly) a defect. Same arithmetic, same fix — I closed it in the verdict and left
        # the two other places it appears.
        return int(Fraction(100 * self.score, self.max_score))


@dataclass(frozen=True)
class GradingItem:
    """What the judge scores: a criterion PLUS its verified evidence (a `ContextBundle` of the
    criterion's grounded anchors). The judge reads `.id`/`.statement`/`.guidance` and MAY read
    `.context` — the evidence-rich seam the future real judge consumes. It sees only grounded
    criteria; it never influences the grounding decision. `FixtureJudge` ignores `.context`."""
    criterion: _crit.Criterion
    context: "lib.ContextBundle"
    levels: tuple = ()          # the behaviorally anchored scale: `spec.Level[]`, score 0..N with a
    #                             descriptor each. A rubric judge scores by MATCHING a descriptor,
    #                             which is what makes two raters agree; a bare 0-10 has nothing to
    #                             agree about.

    @property
    def id(self) -> str:
        return self.criterion.id

    @property
    def statement(self) -> str:
        return self.criterion.statement

    @property
    def guidance(self) -> str:
        return self.criterion.guidance

    @property
    def max_score(self) -> int:
        return self.levels[-1].score if self.levels else 10


@dataclass(frozen=True)
class Verdict:
    decision: str                       # "Go" | "Recycle" | "Unavailable"
    score: int | None                   # 0-100, or None when Unavailable (no valid verdict)
    hurdle: int
    lines: tuple[Line, ...]             # grounded criteria, scored (empty when Unavailable)
    unavailable: tuple[tuple[str, str], ...] = ()   # (criterion_id, reason) that blocked a verdict
    calibrated: bool = False            # did the JUDGE clear a measured agreement bar on THIS rubric?
    #                                     Grounding makes the citations un-fakeable; it says nothing
    #                                     about whether the scorer agrees with a human. Only a judge
    #                                     carrying a Calibration for this exact rubric digest sets it.

    @property
    def defects(self) -> tuple[Line, ...]:
        """The Recycle reasons: criteria scoring below the bar, each with a grounded, un-fakeable
        citation. Compared as an EXACT fraction of the criterion's own scale, so a 0-5 rubric and a
        0-10 one mean the same thing at the same hurdle and no criterion crosses the bar purely by
        rounding. The threshold tracks the verdict policy."""
        return tuple(sorted((l for l in self.lines if l.score * 100 < self.hurdle * l.max_score),
                            key=lambda l: Fraction(l.score, l.max_score)))

    @property
    def ungrounded(self) -> tuple[str, ...]:
        """Deprecated compat shim for the pre-fail-closed field: the ids that blocked the verdict.
        Prefer `unavailable`, which also carries each id's reason."""
        return tuple(cid for cid, _ in self.unavailable)


def _explicit_gap(cfg, sc, require_stamp, today) -> str | None:
    """The reason an `explicit` field of this criterion no longer quotes its source, or None.

    Field-level grounding is not decoration: an `explicit` statement is the ONLY field whose text is
    itself the claim, so if its quote has rotted the judge would score words the source no longer
    contains. Returned as an abstention reason, not raised, because at review time a rotted rubric
    is an evidence gap to report — the authoring check is where it is an error to fix."""
    for f in _spec._all_fields(sc):
        if f.kind != "explicit":
            continue
        try:
            _spec._must_quote(cfg, f, f"criterion {sc.id!r} explicit field", require_stamp, today)
        except _spec.SpecError as e:
            return str(e)
    return None


def review_draft(cfg, draft: str, dimension: str, judge, *, hurdle: int = 60,
                 require_stamp: bool = True, today=None) -> Verdict:
    """Fail-CLOSED: EVERY criterion must have current, unambiguous evidence or the verdict is
    "Unavailable" — never Go/Recycle. Dropping an ungrounded criterion and renormalizing the average
    (the old behavior) could turn a Recycle into a Go, so a gate that loses evidence must abstain,
    not pass. Only when the whole rubric grounds does the judge score and a threshold verdict issue.

    Secure by default: `require_stamp=True` — an UNSTAMPED source (no `source_sha256`, so no trusted
    baseline against tampering) does not ground. Stamp sources with `klode build --stamp`; a caller
    with an intentionally unstamped corpus can pass `require_stamp=False`."""
    # Integer-ness is not pedantry here: the displayed score is the FLOOR of the exact mean, and
    # `floor(x) >= h <=> x >= h` holds only when h is an integer. A float hurdle would let the
    # number shown and the decision reached disagree again, which is the defect below.
    if isinstance(hurdle, bool) or not isinstance(hurdle, int):
        raise ValueError(f"hurdle must be an integer percentage, got {hurdle!r}")
    if not 0 <= hurdle <= 100:
        raise ValueError(f"hurdle must be in 0..100, got {hurdle}")
    # Structure + envelope at load (`corpus=False`) so that a citation which has rotted becomes the
    # abstention this verdict exists for rather than an exception. But the corpus checks are NOT
    # skipped: the fingerprint and every scoring-relevant field are checked below and converted into
    # `Unavailable`. Skipping them entirely let a rubric score on a moved corpus, and let an
    # `explicit` statement whose quote had rotted still be handed to the judge — both directly
    # contradicting the "current evidence, corpus-pinned" guarantee this module advertises.
    spec = _spec.load(cfg, dimension, corpus=False)
    if not spec.approved:
        # Agents generate candidates; only a human promotes them to canon. A gate that scores
        # against an unapproved rubric launders a machine's draft into an authoritative verdict.
        raise _spec.SpecError(
            f"rubric for {dimension!r} is {spec.admission!r}, not 'human_approved' — the gate will "
            "not score against a rubric no human has signed off")
    unavailable: list[tuple[str, str]] = []
    try:                                                # the rubric's corpus pin, as an abstention
        _spec.check_fingerprint(cfg, spec)
    except _spec.SpecError as e:
        return Verdict("Unavailable", None, hurdle, (), (("<rubric>", str(e)),))

    grounded: list[tuple[_crit.Criterion, _crit.Grounding, object]] = []
    for sc in spec.criteria:
        c = sc.to_criterion()
        # Ground each anchor against the card it was DECLARED against. Searching the whole panel let
        # a citation filed to `structure` quietly resolve in `brevity` — a rotted citation migrated
        # to a neighbouring source instead of failing, which is provenance in name only.
        g = _crit.ground_bindings(cfg, c, sc.bindings(), require_stamp=require_stamp, today=today)
        if not g.grounded:
            unavailable.append((c.id, g.resolution or "not-grounded"))
            continue
        bad = _explicit_gap(cfg, sc, require_stamp, today)
        if bad:                                         # an explicit quote that no longer holds
            unavailable.append((c.id, bad))
            continue
        grounded.append((c, g, sc))
    if unavailable:                                     # any missing/stale/ambiguous evidence -> no verdict
        return Verdict("Unavailable", None, hurdle, (), tuple(unavailable))
    # hand the judge each grounded criterion WITH its verified evidence (the seam for a real judge).
    items = [GradingItem(c, lib.build_context_bundle(
                cfg, [(card, marker) for marker, card, _ in g.anchors],   # the full Marker — selector preserved
                require_stamp=require_stamp, today=today), sc.levels) for c, g, sc in grounded]
    # fail-CLOSED on evidence, end to end: a criterion may resolve (decision) yet have no SHOWABLE span
    # (e.g. a folded/cross-line match). The judge must never score without the evidence it cites, so if
    # any criterion's bundle carries a rejection, abstain rather than issue a verdict on absent evidence.
    gaps = [(it.criterion.id, it.context.rejected[0].resolution.value) for it in items if it.context.rejected]
    if gaps:
        return Verdict("Unavailable", None, hurdle, (), tuple(gaps))
    max_by_id = {c.id: sc.max_score for c, _, sc in grounded}
    by_id: dict = {}
    for s in judge.score(draft, items):
        if s.criterion_id in by_id:
            raise ValueError(f"judge returned a duplicate score for {s.criterion_id!r}")
        if s.criterion_id not in max_by_id:              # a phantom/unknown criterion id is a fail-loud error
            raise ValueError(f"judge scored an unknown criterion {s.criterion_id!r}")
        top = max_by_id[s.criterion_id]
        # levels are discrete descriptors, so a score must name one: 0.6 and True match no band
        if isinstance(s.score, bool) or not isinstance(s.score, int):
            raise ValueError(f"judge score for {s.criterion_id!r} must be an integer naming a "
                             f"behavioral level, got {s.score!r}")
        if not 0 <= s.score <= top:                      # each criterion has its OWN behavioral scale
            raise ValueError(f"judge score for {s.criterion_id!r} out of range 0..{top}: {s.score}")
        by_id[s.criterion_id] = s
    missing = max_by_id.keys() - by_id.keys()
    if missing:
        raise ValueError(f"judge did not score grounded criteria: {sorted(missing)}")
    lines = tuple(Line(c, by_id[c.id].score, by_id[c.id].note, g, sc.max_score)
                  for c, g, sc in grounded)
    # ask the judge whether it has been measured against humans on THIS rubric; a judge with no
    # such method cannot claim it, which is the correct answer for the stub
    calibrated_for = getattr(judge, "calibrated_for", None)
    calibrated = bool(calibrated_for and calibrated_for(_spec.rubric_identity(spec)))
    # Weight each criterion equally by normalizing to its own scale first: summing raw scores would
    # silently let a 0-10 criterion outvote a 0-5 one purely by having more room. Average the EXACT
    # ratios — rounding each criterion's percentage first accumulated error that flipped verdicts
    # at the hurdle (maxima (2,3), scores (0,2): 34 rounded-then-averaged vs 33).
    exact = 100 * sum(Fraction(l.score, l.max_score) for l in lines) / len(lines)
    # The threshold is applied to the EXACT mean, never to a display value. Rounding first promoted
    # every draft in [hurdle-0.5, hurdle) to Go: a 0..3 / 0..7 rubric scored 1 and 6 is exactly
    # 59.5238% and passed a hurdle of 60. The error ran one way only — 12 false Go across the
    # two-criterion rubrics with maxima 2..10, and no false Recycle, because a false Recycle is
    # arithmetically impossible. `defects` below already compared exactly, with a comment warning
    # against crossing the bar by rounding; the verdict itself was still doing it.
    decision = "Go" if exact >= hurdle else "Recycle"
    # FLOOR for display, not round: for an integer hurdle `floor(x) >= h <=> x >= h`, so the number
    # shown can never contradict the decision reached. Rounding contradicts it in 1338 of those same
    # rubrics. This is a theorem given the integer check at the top of this function, not a taste.
    total = int(exact)                       # Fraction -> int truncates toward zero; exact >= 0
    return Verdict(decision, total, hurdle, lines, (), calibrated)
