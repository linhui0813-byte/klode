# Stage-Gate criteria, grounded in Cooper — the gate half of Loop B

**Status:** design-note / primary-source grounding. **Date:** 2026-07-28.
**Scope:** The "workflow + stage gates + criteria" half of the supervising agent, grounded in the
primary source rather than web summaries: Robert G. Cooper, *Winning at New Products* (chs. 2, 4, 8,
9). Companion to
[`supervising-agent-architecture-2026-07-28.md`](supervising-agent-architecture-2026-07-28.md), which
frames the two-loop design; this note supplies Loop B's gate schema and a worked, grep-anchorable gate
card.

All quoted phrases below are **exact strings** from the source, chosen to be usable as lodlib
`(grep: "…")` anchors — the same "cite, don't recall" discipline the library enforces. Reading closely
corrected three things the earlier web-based summary got wrong or thin (noted inline).

---

## 1. What Cooper actually says

**Gate anatomy** — three components (ch. 9), verbatim structure:
`(grep: "Inputs (deliverables)")` · `(grep: "Decision criteria")` · `(grep: "Outputs—Go/Kill/Hold/Recycle")`.

**Three criterion types** (correction: the earlier summary used two — Cooper uses three, and the
distinction is exactly what a judge needs):

| Type | What it is | Verdict effect |
|---|---|---|
| **Must-meet** | "preliminary Yes/No or knockout questions" — strategic mandate, ethics/legality, showstoppers, capability | a single **No** → Kill; it "weed[s] out obvious losers," never gives "a strong green light" |
| **Go/Kill** | quantitative hurdles (his domain: NPV, IRR>30%, payback<3yr) | failing any one → Kill |
| **Should-meet** | "highly desirable project characteristics," scored on a scorecard | scored, not fatal; must clear a hurdle `(grep: "sixty to sixty-five points")` out of 100 |

**Five decisions** (correction: earlier gave four — Cooper allows a *Conditional Go*): Go ·
Conditional Go · Kill · Hold · Recycle. Two definitions are load-bearing:

- **Hold** — "the project passes the gate criteria—it's an OK project—but that better projects are
  available or resources are not available." A Hold is a *prioritization* decision, not a quality one.
- **Recycle** — "analogous to 'rework' on a production line… signals that the project team has not
  delivered what was required."

**The three-part diamond** (new; the earlier summary missed it): a gate first runs a **readiness
check** on deliverable quality — and `(grep: "the decision is not Kill, but rather Recycle")` when it
fails. It then debates **"quality of execution"** and **"business rationale"** *separately*, so a good
job on a doomed project isn't scolded and a sloppy job on a great idea isn't rubber-stamped.

**Gates with teeth** — the spine of ch. 9, and the most important idea for *self*-supervision. Most
organizations' gates are `(grep: "express trains")` that never stop; `(grep: "the Kill option is
rarely exercised")`. His fix is a discipline he bluntly calls learning to
`(grep: "drown some puppies")`. Read against this use case: **a supervising agent that can only say Go
is worthless; its entire value is its willingness to say Recycle / Hold / Kill.**

**Two source lists to distill into criteria:**

- *Eight critical success drivers* (ch. 2, Table 2.2) → **quality principles**: `(grep: "unique,
  superior, differentiated")` product · voice-of-customer · front-end homework · `(grep: "sharp and
  early product and project definition")` · spiral (build-test-feedback-revise) · world/glocal product
  · launch + marketing plan · `(grep: "not at the expense of quality of execution")`.
- *Six scoring-model factors* (ch. 8) → **should-meet scorecard**: strategic fit · product advantage
  (`(grep: "number one driver")`, ~26% of profits) · `(grep: "Leverages core competencies")` ·
  market attractiveness · technical feasibility · risk-and-return.

**The success-criteria method** (P&G, ch. 8) → the missing calibration loop: pre-agree
`(grep: "what would the project have to achieve in order that it be considered 'a success'")`, then
re-judge at every gate *and* at a Post-Launch Review. This is the per-stage definition-of-done plus a
retrospective — the exact feedback loop the architecture note flags as absent from lodlib today.

## 2. What transfers, what to drop

*Winning at New Products* is about a **company allocating scarce R&D budget across a portfolio of
rival product projects.** A solo creator supervising their *own* work has one project at a time and no
shared budget. Do not parrot the apparatus — translate it.

| Cooper mechanism | For the supervising agent | Why |
|---|---|---|
| Gate anatomy: deliverables → 3 criteria types → decision | **Keep whole** | Domain-agnostic; this *is* the gate schema |
| Go / Kill / **Hold** / **Recycle**, readiness-first | **Keep whole** | Recycle-not-Kill on a readiness fail is how a work-supervisor should behave |
| "Gates with teeth" / drown the puppies | **Keep — make it the design goal** | A self-supervisor's failure mode is all-Go; force the agent to be able to Hold/Kill |
| Eight success drivers | **Keep as principles**, adapt 2 | "World/glocal product" and "launch marketing plan" are product-specific — translate to audience/reach fit, or drop |
| Scorecard factors + ~60/100 hurdle | **Keep, relabel** | Strategic-fit→fits the body of work; product-advantage→distinctively good; feasibility→can you pull it off |
| **Success criteria** method + Post-Launch Review | **Keep — this is the missing feedback loop** | Per-stage definition-of-done plus a retrospective/calibration point |
| NPV / IRR / Productivity Index / ECV / strategic buckets / Monte Carlo / bubble diagrams | **Drop** (or reduce to one crude effort-vs-payoff score) | Portfolio math for multi-project budget allocation; irrelevant to a single project + one's own time |

Deepest transferable idea, in his words: gate decisions are `(grep: "a series of options decisions")`
— `(grep: "you buy discrete chunks of the project at each gate")`. The antidote to committing a year
to a manuscript before a single checkpoint: **stage-gate the work so each stage is a cheap option,
not an all-in bet.**

## 3. A worked gate card (proposed new artifact for the `_syntheses` layer)

This is a *proposed new artifact kind* — a `kind: gate-criteria` card — not an existing lodlib type.
It shows Cooper's Gate 3 (commit-to-full-development) translated to a writing/book project, in the
library's card format, every criterion `(grep:)`-anchored to the source so `lib check` guards it:

```markdown
---
id: gate-3-greenlight-full-draft
kind: gate-criteria
source: cooper-winning-at-new-products
stage_entered_from: "Concept & Research (homework)"
scorecard_hurdle: 60            # of 100; below → Recycle/Hold  (grep: "sixty to sixty-five points")
zoom: full
---
# Gate 3 — Greenlight the Full Draft

## Readiness check  (fail → Recycle, NOT Kill)   (grep: "the decision is not Kill, but rather Recycle")
- [ ] Research/homework actually done, not asserted   (grep: "front-end loading the project")
- [ ] One-line concept is sharp and written down      (grep: "sharp and early product and project definition")

## Must-meet — knockout, any No → Kill   (grep: "a single No can signal a Kill decision")
- [ ] Fits the body of work / long-term direction     (grep: "within the strategic mandate")
- [ ] No ethical/legal/factual showstopper            (grep: "evident showstoppers or killer variables")
- [ ] Executable at this scope                          (grep: "capable of undertaking the project")

## Should-meet — score 0–10 each, must total ≥ 60/100
| Factor (Cooper's scorecard)     | grep anchor                       | score |
|---------------------------------|-----------------------------------|-------|
| Distinctive / superior          | "unique, superior, differentiated"| _/10  |
| Plays to your strengths         | "Leverages core competencies"     | _/10  |
| Real audience / need            | "Market attractiveness"           | _/10  |
| Feasible (gap/complexity/risk)  | "Technical feassiblity"           | _/10  |
| Effort vs. payoff               | "Risk and return"                 | _/10  |

## Decision  (grep: "Go, Kill, Hold, or Recycle")
Go · Conditional Go · Recycle (fix and re-present) · Hold (fine, but not now) · Kill

## Success criteria — agree NOW, judged again at the Post-Launch Review
# (grep: "what would the project have to achieve in order that it be considered 'a success'")
- e.g. "finished draft by <date>", "N beta readers rate it clearer than the last book"
```

Note the `Technical feassiblity` anchor preserves the source's spelling — anchors match the source
*as printed*, not as it should have been spelled, which is the whole point of literal grep.

## 4. How it plugs into lodlib

- The card lives in the `_syntheses` layer (or a sibling `_gates/`) once `[frameworks] enabled = true`.
- Its anchors are guarded by `lib check` (check F) against an ingested copy of the Cooper source — so
  the *criteria themselves* cannot silently drift from the book they claim to encode.
- A future `review_draft` verb (see the architecture note, §4) reads this card, scores a draft against
  the should-meet table, runs the must-meet knockouts, and emits the Go/Recycle verdict with each
  defect grep-grounded — turning lodlib's guard from "guarding the library" into "grounding the judge."

The supervising agent does not invent standards; it applies Cooper's, and every standard it cites is
grep-verifiable against the actual book. That is the join between the knowledge-base substrate and the
supervision layer, made concrete.

---

## Source

Robert G. Cooper, *Winning at New Products* (chapters used: 2 — eight success drivers; 4 — the
Stage-Gate system and its five artefacts; 8 — scoring models and success criteria; 9 — "Making the
Gates Work — Gates with Teeth"). Quoted phrases are exact strings for use as literal anchors; to make
them enforceable, ingest the source onto a shelf and let `lib check` verify each anchor resolves.
