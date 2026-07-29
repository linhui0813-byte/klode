# loopb — a supervising agent (Loop B) as a thin consumer of lodlib

**Status:** walking skeleton (2026-07-28). Proves the architecture end-to-end; the LLM judge and the
calibration loop are the next pieces, not this one.

lodlib is **Loop A** — it encodes expertise as grep-grounded, citable knowledge. `loopb` is **Loop B**
— it *supervises work*: submit a draft, score it against grounded criteria, return a Cooper-style
verdict (**Go / Recycle**). It is a separate package that imports only the `lodlib` facade; it never
reaches into lodlib internals.

## The one mechanism this proves

A source-grounded judge's #1 failure mode is *"cited but not verified"* — it names a source that
doesn't support the claim. `loopb` closes that structurally: **every cited defect is grounded through
`lodlib.verify`** — the same literal-grep verifier the citation-rot linter uses. A criterion whose
citation does not resolve in a real source is *dropped and flagged*, never scored. The judge does not
get to invent a citation (`test_a_fabricated_citation_does_not_ground` proves it). A Loop B built on
lodlib is therefore more trustworthy than one built on plain RAG — that is the whole point of building
it here.

## The chain

```
lodlib.consult(dimension, projection="writer")   # load the Craft-layer moves
      -> criteria (each = a move + its verbatim source phrases)
      -> ground each via lodlib.verify           # un-fakeable: phrase must resolve in a panel source
      -> judge.score(draft, grounded_criteria)   # rubric scores, 0-10 each  (← the LLM judge plugs in)
      -> Cooper verdict: %>=hurdle -> Go, else Recycle with the low-scoring, GROUNDED defects
```

Run it:

```
python3 demo.py                 # reviews an info-dump draft against the `worldbuilding` dimension
python3 -m unittest test_loopb  # the chain, proven deterministically with a FixtureJudge
```

Demo output (abridged): `VERDICT: Recycle 59/100` with each defect carrying a verified citation, e.g.
*"Price the world — spend only on deviation" [2/10] → grounded: "The status quo does not need world
building…" → elliott-2013…:1*.

## What is real vs. what plugs in

| Piece | State |
|---|---|
| Criteria loading from a lodlib dimension's Craft layer | **real** (`criteria.load_criteria`) |
| Grounding every citation via `lodlib.verify` | **real** — the un-fakeable step |
| Cooper Go/Recycle verdict + should-meet scorecard | **real** (`review.review_draft`) |
| The rubric **judge** | **stub** (`FixtureJudge`) — the real LLM judge (G-Eval two-step form-filling, position-bias-debiased via balanced permutation, a *different* model than the author, **calibrated against a human gold set**) drops into the `Judge` protocol, same `score()` signature |

## Not done yet (deliberately)

- **Calibration** — a 20–50 draft human-scored gold set; measure judge-vs-human agreement; ship no gate
  until it clears the bar. This is the make-or-break for a *supervisor* and comes before trusting any
  verdict.
- **Must-meet knockouts** — the skeleton demonstrates should-meet scoring only. Cooper's must-meet
  (single No → Kill) and Hold belong on a dedicated `kind: gate-criteria` card.
- **A dedicated gate card grounded in a method source** (e.g. Cooper's *Winning at New Products*, once
  ingested) — the skeleton reuses a dimension's Craft moves as criteria, which is enough to prove the
  chain but is not a purpose-built gate.
- **The human at the gate** — the agent *recommends* Go/Recycle; the human commits it.

## Dependency

`import lodlib` (the facade: `Config`, `consult`, `dimension`, `verify`). A real deploy would
`pip install lodlib`; the demo/tests add the sibling `../lodlib` checkout to `sys.path`.
