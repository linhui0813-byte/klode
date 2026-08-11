# `klode.gate` — a supervising agent (Loop B) as a thin consumer of `klode.lib`

**Status:** the chain is real end to end; the one thing missing is *calibration measurement*, which
needs human raters rather than code. Last substantive change 2026-08-09.

`klode.lib` is **Loop A** — it encodes expertise as grep-grounded, citable knowledge. `klode.gate`
is **Loop B**: submit a draft, score it against an authored rubric, return a Cooper-style verdict
(**Go / Recycle / Unavailable**). It imports only the `klode.lib` facade and never reaches into its
internals; `tests/test_layering.py` enforces that.

## The one mechanism this proves

A source-grounded judge's #1 failure mode is *"cited but not verified"* — it names a source that
does not support the claim. The gate closes that structurally: **every cited defect is grounded
through `klode.lib.verify_evidence`**, the same literal-grep verifier the citation-rot linter uses.
A criterion whose citation does not resolve — freshly, unambiguously, in the card it was *declared*
against — is never scored. The judge does not get to invent a citation
(`test_a_fabricated_citation_does_not_ground`).

## The chain

```
spec.load(dimension)              # the authored rubric — the gate's SOLE input
  -> check_fingerprint            # the corpus has not moved since the rubric was pinned
  -> ground_bindings              # each anchor against the card it was DECLARED against
  -> _explicit_gap                # every `explicit` quote still occurs, verbatim, in a raw line
  -> judge.score(draft, items)    # per-criterion score on that criterion's own 0..N scale
  -> Cooper verdict               # mean % >= hurdle -> Go, else Recycle with grounded defects
```

Any evidence failure yields **Unavailable** — never Go/Recycle. A criterion is never dropped and the
average renormalized, because that could turn a Recycle into a Go: a gate that gets *safer* when it
loses evidence is inverted.

Run it:

```bash
python3 examples/gate_demo.py            # reviews an inert draft against the fixture `pacing` rubric
python3 -m pytest tests/ -q              # the whole chain, deterministic, no network
```

## History

> **Hardening (2026-08-02).** Grounding became freshness-aware (`verify_evidence`, not the
> occurrence-only `verify`), and the verdict became fail-CLOSED with `Unavailable` as a third state.
> `tests/test_gate_hardening.py`.

> **Rubric (2026-08-07).** The gate's sole input is now **CriterionSpec v1** — corpus-pinned,
> human-approved, stable ids, field-level epistemics
> (`explicit`/`paraphrase`/`derived`/`operator_policy`/`unknown`), behaviorally anchored levels. It
> replaces promoting a synthesis's bold Craft bullets into positional `C1`/`C2` criteria, which made
> the rubric an accident of prose order and let a `statement` contradict its own anchor and still
> ground. Craft-move loading survives as the *authoring seed*
> (`python3 -m klode.gate derive`). See [`SPEC-criterion.md`](SPEC-criterion.md).

> **Judge (2026-08-09).** `LLMJudge` replaces the stub: G-Eval two-step form-filling (steps derived
> before the draft is in view), balanced permutation over reversed level orders against position
> bias, injectable transport, stdlib-only. Crucially it is **calibration-gated** — see below.

## What is real

| Piece | State |
|---|---|
| CriterionSpec v1: load, fail-closed validate, admission gate | **real** — `gate/spec.py` |
| Authoring: derive a candidate, pin the corpus, approve, re-pin | **real** — `gate/authoring.py`, `python3 -m klode.gate` |
| Grounding every citation, per declared card | **real** — the un-fakeable step |
| Cooper Go / Recycle / Unavailable verdict | **real** — `review.review_draft` |
| The rubric **judge** | **real** — `gate/llm_judge.py`; `FixtureJudge` remains for deterministic tests |
| Inter-rater agreement as the rubric's acceptance test | **real** — `eval/rate.py` |
| Judge-vs-human **calibration measurement** | **not done** — see below |

## The calibration gate, and why it is the honest part

An `LLMJudge` runs whether or not it has been calibrated — you need it to run in order to calibrate
it. What it *cannot* do is present an uncalibrated verdict as authoritative:

- `Calibration` is a **record**, not a flag: a rubric digest, an `n`, and a measured
  quadratic-weighted kappa against human scores. There is no boolean to set.
- `calibrated_for(digest)` answers for **one specific rubric**. Reword a level descriptor and
  `rubric_identity()` changes, so an agreement number measured on the old wording no longer
  transfers. Recalibration is required, not optional.
- `Verdict.calibrated` records the answer, and `_svc_review` sets `non_production = not calibrated`
  — tracking a fact instead of the hardcoded `True` that was right for the stub and would have
  quietly stayed true for a real judge.

**No rubric has been measured yet.** Until one is, every verdict this gate issues is explicitly
non-production. That is the make-or-break for a supervisor, and it is a human-rater task.

## Still owed

- **Calibration measurement** (above) — the blocking item.
- **Must-meet knockouts.** Only should-meet scoring exists. Cooper's must-meet (a single No → Kill)
  and Hold need their own criterion kind. `criticality: advisory` is currently *rejected* at parse
  rather than silently ignored, because a label with no behaviour is a lie.
- **A rubric from a method source** (e.g. Cooper's *Winning at New Products*) rather than from a
  craft dimension. CriterionSpec is the purpose-built artifact; the method-grounded instance is owed.
- **The human at the gate.** The agent *recommends* Go/Recycle; the human commits it.

## Dependency

`from klode import lib` — the facade (`Config`, `consult`, `verify_evidence`, `verify_context`,
`build_context_bundle`, `source_digest`, `Marker`, `parse_markers`). Nothing else.
