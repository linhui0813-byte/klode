# Consumer-audience projection for `_syntheses` (craft vs engine vs provenance)

**Status:** proposal / dev-note. Written 2026-07-24 while using the `craft-library` MCP as a
*consumer* — grounding a novel-in-progress (a social-realist crime novel) on the dimension
syntheses. This is the view from that consumer's seat.

## The observation

`consult_dimension("curiosity")` returns the entire synthesis document. For the use case it was
built for — specifying/vetting the doxai scoring engine — that is correct: every line is
load-bearing. For a *second* consumer — a writer asking "why do readers put my book down in
chapters 3–4, and what do I do about it?" — roughly **40% of the returned text is the craft answer
and ~60% is noise**:

- **Engine-spec noise (~30%):** `scorers.mystery`, `curiosity=True`, `_gap_perceptibility`,
  `CUR_REF_K`, `spec/05`, "scales the `0.3·anachrony` surprise term by `(1 − gap-perceptibility)`".
  This is the *implementation* of the claim inside a computational scorer. To a writer it carries
  zero information; the same claim's craft form is one sentence — "a **marked** gap reads as
  curiosity, a **concealed** gap reads as surprise."
- **Provenance/audit noise (~30%):** the skeptic-gate row, `Delon gate`, `ε-test` falsification
  designs, `Owed / field-search obligations`, `RT-08`/`RT-13`, "5 rounds, 0→5 PASS, promoted
  2026-07-23". This is the *audit trail* — the reason the claim is trustworthy. It is the moat, not
  filler. But it is meta-information about the claim's *status*, not the claim.

The craft payload — Loewenstein's information-gap mechanism, the awareness precondition, the five
opening operators, "intensifies toward closing," Kuhn/Barnhart misdirection — is real and
excellent. It is just buried in a document written for two other readers.

## Root cause

A `_syntheses` document currently fuses three layers into one body of prose:

- **L-craft** — the claim a writer/editor can act on: mechanism, operators (*what to DO*), the
  preserved disagreements, the verified quotes.
- **L-engine** — how the claim maps onto `scorers.py` (the reason the library was born: to feed a
  computational narrative engine).
- **L-audit** — how the claim was adversarially vetted, and what remains unread/contested.

`consult_dimension` has no projection to L-craft alone. It has a `section=` param, but the syntheses
have no clean writer-only section to select — the craft is *interleaved* with engine references
inside sections like "The synthesis" and "The genuine tensions."

**This is a regression from lodlib's own philosophy.** The README's core promise is "*pull the
cheapest layer that answers your question; zoom only when you must*" — and LOD zoom delivers exactly
that for cards (L0/L1/L2/L3). `consult_framework` already honors it: it defaults to
`engine+practices+disagreement` and calls practices "the most useful part." The `_syntheses` are the
one surface where that discipline lapsed: the dimension tool hands back the whole multi-audience
document, and the consumer does the projection by hand every time.

## Proposal — apply lodlib's own layering to the syntheses

### 1. Format convention (SPEC.md — the `_syntheses` contract)
Require each synthesis to carry its craft payload in a self-contained, **engine-free** section, and
quarantine the other two layers:

- `## Craft` (or `## For the writer`) — mechanism + operators/what-to-DO + preserved disagreements
  + the verified quotes that back them. **No `scorers.*`, no `RT-*`, no ε-test.** Must stand alone.
- `## Engine` — the scorer mapping (`scorers.mystery`, `_gap_perceptibility`, calibration knobs).
- `## Provenance` — tier + one-line status, the skeptic-gate verdict, `Owed`, falsification
  designs, promotion history.

The one-line `Tier:` and `Core question:` stay at the top — both audiences need them.

### 2. Tool projection (query.py / mcp_server.py)
`consult_dimension(dimension, audience="writer" | "engine" | "full", section=None)`:
- **`writer` (default):** `Core question` + `Tier` + `## Craft` only.
- **`engine`:** `## Craft` + `## Engine`.
- **`full`:** the whole document (today's behavior).

Mirror it where relevant on `consult_framework` (already close — it just needs to keep engine
symbols out of `practices`).

### 3. Optional — a problem-first entry point
`apply_dimension(dimension, problem: str)` — take a concrete craft problem ("readers quit in
chapters 3–4; the buried mystery isn't pulling") and return only the operators that bear on it,
phrased as moves. This is exactly the manual projection a consumer performs today; making it a tool
removes the hand-work and the temptation to paraphrase (and thus rot) a claim.

### 4. Keep the audit — layer it, do not delete it
The provenance apparatus is *why* these syntheses beat a generic RAG summary — it is the
citation-rot ethos applied to synthesis-level claims. The fix is never to strip it; it is to fold it
under `## Provenance` and return it only when asked. A consumer who wants "how sure are you?" asks
for it; one who wants "what do I do?" should not have to wade through it.

## Smallest first step
Add a required `## Craft` section to the `_syntheses` format in SPEC.md; backfill it for the ~16
existing dimension syntheses (a mechanical lift — the craft prose already exists; it just needs to
be gathered engine-free); make `consult_dimension` default to returning that section. That single
change converts the tool from "a spec sheet for the engine" into "a consultant for the writer"
without touching the engine or the audit.

## Why this matters now
The trigger was real use. The `craft-library` MCP is being consumed by an actual novel project,
where a human editor routes concrete reader-problems (from a blind-read panel) to dimensions and
needs the *moves*, not the scorer math. That is a second, legitimate audience the library already
serves over MCP but was not authored for. The projection above is what turns "a knowledge base that
happens to be queryable" into "a knowledge base that answers the question you asked."
