# klode

A **klode** — a rich vein — of grounded, verifiable knowledge, and the machinery to encode it and to
*supervise work* against it. Everything a claim asserts is anchored to a verbatim source, checked by a
fail-closed linter, so the system can't quietly drift or hallucinate: **cite, don't recall.**

Three components, one repo:

```
klode/
  klode/
    lib/       # the ENGINE (Loop A) — a grep-grounded, level-of-zoom knowledge library
               #   + a citation-rot linter. Zero runtime deps. Import: `klode.lib`; CLI: `klode`.
    gate/      # the SUPERVISOR (Loop B) — review_draft: score a draft against grounded criteria
               #   and issue a Cooper Go/Recycle verdict whose every cited defect is verified
               #   through klode.lib.verify (a plain-RAG judge cannot do that). Import: `klode.gate`.
  corpus/
    kb-01-storycraft/   # the first knowledge base (narratology / craft / worldbuilding)
    kb-02-…/            # more KBs — the engine runs per-KB via `-c <kb>/library.toml`
  eval/        # retrieval / token / benchmark harnesses for the engine
  examples/    # examples/gate_demo.py — the supervisor over kb-01
  dev-docs/    # the design record: the two-loop architecture, plans, reviews, SPEC.md
```

## The two loops

- **Loop A — encode expertise** (`klode.lib`): turn sources into cited, retrievable knowledge. Every
  card claim carries a verbatim `(grep: …)` anchor; `klode check` fails if any citation stops resolving.
- **Loop B — supervise work** (`klode.gate`): submit a draft, score it against criteria loaded from a
  KB's craft layer, and return **Go / Recycle** — each cited defect grounded through `klode.lib.verify`,
  so the judge's citations are un-fakeable.

The boundary is enforced: `klode.gate` consumes only the `klode.lib` public API (facade), never its
internals (`tests/test_layering.py` guards it).

## Quickstart

```bash
klode -c corpus/kb-01-storycraft/library.toml check          # validate a KB (citation-rot linter)
klode -c corpus/kb-01-storycraft/library.toml consult worldbuilding   # read a craft lens
python examples/gate_demo.py                                # supervise a draft against kb-01
python -m unittest discover -s tests                        # the full suite (153 tests)
```

## Status

- `klode.lib` — solid: 145 tests, a stable public-API facade, an AST layering guard, clean-venv wheel smoke.
- `klode.gate` — a **walking skeleton** (8 tests): the chain and the un-fakeable-citation mechanism are
  proven; the rubric **judge is a stub** (`FixtureJudge`) — the real LLM judge (G-Eval two-step,
  debiased, calibrated against a human gold set) plugs into the `Judge` protocol. Calibration is the
  next real build. See `dev-docs/`.
