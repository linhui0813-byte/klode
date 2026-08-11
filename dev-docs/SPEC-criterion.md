# CriterionSpec v1 — the gate's sole input

**Status:** shipped (2026-08-07). Schema id: `klode.criterion-spec/v1`.
Implementation: `klode/gate/spec.py` (load + validate), `klode/gate/authoring.py` (derive + pin),
`python3 -m klode.gate` (CLI), `eval/rate.py` (the acceptance test).

## The defect this closes

The walking skeleton built its rubric by promoting a synthesis's bold Craft bullets into criteria.
Three things were wrong with that, and only the third is obvious in hindsight:

1. **Ids were positional.** `C1`, `C2`, … came from `len(crit) + 1`. Reordering two bullets renamed
   the rubric, which silently voids every human label ever collected against an id.
2. **The `statement` was not the source.** It was the bullet's bold head — an *imperative summary*.
   Useful, but not a quotation, and nothing said so.
3. **Object-level provenance covered a mixed bag.** One `(grep: …)` anchor sat at the end of a
   bullet whose statement, guidance, and implied scope were all authored. The anchor resolved, so
   the whole object looked quote-grade.

The consequence is exact, and it is the reason this artifact exists:

```
statement: "Keep every redundant clause; never trim."
anchor:    "Trim every clause the reader can infer"        → grounds clean
```

`ground()` proves the *anchor* resolves. It never compared the *statement* to it. A rubric could
assert the opposite of its source and pass every check klode had.

## The epistemic envelope

Every authored text value is an object, not a string, and its `kind` is mechanically enforced:

| kind | meaning | value | warrant | evidence | extra check |
|---|---|---|---|---|---|
| `explicit` | the source's own words | required | **forbidden** | required | **the value itself must occur in a raw source line** |
| `paraphrase` | the source's claim, restated | required | required | required | — |
| `derived` | follows from the source; not stated by it | required | required | optional | — |
| `operator_policy` | the operator's instrument, not the book's | required | required | optional | — |
| `unknown` | the author did not state this | **must be null** | forbidden | forbidden | — |

`explicit` is what makes field-level provenance real: the value *is* the quote, so a statement and
its anchor cannot diverge.

**Exactly what `explicit` proves, and what it does not.** The value must resolve as `FOUND` — an
occurrence in a *raw* source line — never `FOLDED_ONLY`. That distinction is load-bearing, because
the grep normalizer folds hyphenation: under a folded match the source `Writers must re-sign` would
satisfy the explicit value `Writers must resign`, which asserts the opposite. The cost is that an
`explicit` quote cannot span a line break; shorten it, or mark the field `paraphrase`.

**The contradiction is not "unrepresentable" — it is un-disguisable.** A criterion that says the
opposite of its source can still be authored as `paraphrase` with any non-empty warrant, because
warrant *presence* is checked and entailment is not. What the envelope removes is the ability for
such a claim to wear quotation's authority. Catching it is what human approval is for, and why
approval is a gate rather than a formality.

`unknown` is what keeps the schema from manufacturing knowledge. **A required field with no way to
say "not stated" is a manufacturing order** — a mandatory `exceptions:` forces the model to invent
exceptions for a principle whose author stated none. Here, "the author did not state this" is a
first-class, checkable answer.

## Behaviorally anchored levels

Each criterion declares its own `0..N` scale with a **descriptor per level**, because the test of a
criterion is whether two people apply it the same way, and a bare 0–10 gives them nothing to agree
about. Levels must be contiguous from 0 and at least two.

Level descriptors are almost never quotable — a book states a direction, not a five-point scale — so
they are normally `operator_policy`: labelled as the operator's instrument, versioned, and approved.
**Do not quote-shop five source sentences to make a scale look grounded.** That manufactures
provenance rather than removing it. The honest label is the one that says who built the instrument.

Scores are normalized to each criterion's own scale before averaging, so a 0..10 criterion cannot
outvote a 0..5 one purely by having more room.

## Structural guarantees

| rule | why |
|---|---|
| ids match `^(?!\d+$)[A-Za-z][A-Za-z0-9._-]*$`, and no bare slot numbers (`C1`, `c1`, `C1a`) | labels collected against an id must survive a reorder; separators/controls also keep ids safe in paths and terminals |
| ids unique within a spec | a duplicate silently drops one criterion from the rubric |
| every citation names a **panel** card; panel entries unique | provenance stays inside the declared panel; a repeated card grounds twice and reports `ambiguous-panel` |
| **no regex evidence** | `.+` is technically an anchor; a canonical rubric quotes exact words |
| `dimension` matches `^[A-Za-z][A-Za-z0-9_-]*$` | it is a path component — a separator or `..` would escape the criteria directory |
| `fingerprint.sources[card]` matches `^[0-9a-f]{64}$`, covers every panel card, and equals the live digest | a rubric that does not pin its corpus cannot say whether it drifted |
| `criticality` must be `required` | `advisory` is parsed but has **no** behavioural effect — the gate weights and gates every criterion identically, so accepting the word would be a lie |
| every value/warrant carries **visible** text | `strip()` leaves U+200B, so a zero-width space satisfied "non-empty" and a level could validate blank |
| duplicate JSON keys rejected; non-UTF-8, unreadable, oversized (>4 MiB) files raise `SpecError` | `json.loads` keeps the *last* duplicate silently, so a reviewer reading top-down and the parser could disagree about `admission` |
| every type checked before use; a wrong type is rejected, never `or`-defaulted away | `evidence: ""` became `[]` and `fields: []` became `{}` — the wrong value vanished instead of being diagnosed |
| `admission: human_approved` required to score, and `approved_digest` must match the body | agents generate candidates; only a human promotes them — and the digest detects approve-then-edit |

## Where validation runs

Deliberately split, because the two failures are different failures:

- **Authoring / CI — `python3 -m klode.gate check`.** Full corpus validation: `explicit` values
  ground verbatim, the fingerprint matches, every anchor resolves. A rubric whose citations have
  rotted is an *authoring defect*, and this is where the author fixes it.
- **Review time — `review_draft`.** Structure and envelope at load, then the corpus checks that
  bear on scoring: the panel **fingerprint**, each anchor grounded **against its declared card**,
  and every `explicit` field's quote. These are converted into `Unavailable` rather than raised —
  at review time a rotted rubric is an evidence gap to report, and the abstention is what the
  verdict exists for. Skipping them entirely (the first cut of this design) let a rubric score on
  a moved corpus and handed the judge an `explicit` statement whose quote no longer existed.

## The authoring workflow

```bash
python3 -m klode.gate -c LIB derive  <dimension>   # seed a CANDIDATE from the Craft layer
python3 -m klode.gate -c LIB check   <dimension>   # the errors are the worklist
#   ... a human writes the warrants and the level descriptors, sets each field's kind ...
python3 -m klode.gate -c LIB approve <dimension>   # validates, then seals with approved_digest
python3 -m klode.gate -c LIB repin   <dimension>   # after a source changes — RESETS approval
```

**What approval is and is not.** `approve` is *honor-based about the human* — nothing in a file can
prove a person read it — and *mechanical about the content*: `approved_digest` is a canonical hash
of the rubric body, so approve-then-edit is detected rather than inherited. Reformatting the file
does not break the seal; changing a statement, warrant, level, citation, panel, or pin does.
**The digest is unkeyed and stored beside what it signs**, so it catches a careless or automated
edit that does not reseal — it does not resist an agent that recomputes it, and it is not a
signature. If you need that property, enforce it where signatures live (protected branch, code
owners, review), not here. Treat
the human half as a workflow guarantee you enforce outside this tool.

`derive` never invents a warrant or a level descriptor, so its output deliberately does **not**
validate. A machine that could write a convincing warrant is exactly the machine whose output this
artifact exists to keep out of canon. `repin` resets `admission` to `candidate` on purpose: keeping
the approval would let a changed source slip into canon under an old signature.

## Acceptance: the rubric is not done until two people agree

```bash
python3 eval/rate.py sheet -c LIB <dim> --drafts drafts/ --rater alice > .klode-ratings/alice.jsonl
python3 eval/rate.py sheet -c LIB <dim> --drafts drafts/ --rater bob   > .klode-ratings/bob.jsonl
python3 eval/rate.py score .klode-ratings/alice.jsonl .klode-ratings/bob.jsonl --bar 0.6
```

Reports quadratic-weighted kappa **per criterion**, because the aggregate hides what you act on: one
vague descriptor can sink a rubric while every other criterion is fine. The standard is klode's own
(`supervising-agent-architecture-2026-07-28.md` §2.5): *would two domain experts independently reach
the same verdict? If not, the criterion is under-specified — **fix the criterion, not the judge**.*

This is why authoring and labelling are **one loop, not two phases**. There is no way to know a
level descriptor is well defined except to have two people apply it and measure where they diverge.
A spec frozen before anyone rates against it is a guess with a version number.

## What this does not buy

- **Agreement is necessary, not sufficient.** Two raters can consistently apply a criterion that
  measures the wrong thing.
- **It says nothing about the judge.** Judge calibration comes after, against the rubric this
  certifies, and it needs its own gold set. The *mechanism* now exists: `rubric_identity(spec)` is
  the digest a `Calibration` is measured against, `LLMJudge.calibrated_for()` answers for that one
  rubric, and `Verdict.calibrated` drives `non_production`. Rewording any level descriptor changes
  the digest and invalidates the calibration — deliberately, because it is a different instrument.
- **Grounding remains referential.** `explicit` proves the words are the source's; it does not prove
  a `derived` field's warrant is sound. That is what human approval is for, and why it is a gate.
