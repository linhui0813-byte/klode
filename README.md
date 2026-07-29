# lode

A **lode** — a rich vein — of grounded, verifiable knowledge, and the machinery to encode it and to
*supervise work* against it. Everything a claim asserts is anchored to a verbatim source, checked by a
fail-closed linter, so the system can't quietly drift or hallucinate: **cite, don't recall.**

Three components, one repo:

```
lode/
  lode/
    lib/       # the ENGINE (Loop A) — a grep-grounded, level-of-zoom knowledge library
               #   + a citation-rot linter. Zero runtime deps. Import: `lode.lib`; CLI: `lode`.
    gate/      # the SUPERVISOR (Loop B) — review_draft: score a draft against grounded criteria
               #   and issue a Cooper Go/Recycle verdict whose every cited defect is verified
               #   through lode.lib.verify (a plain-RAG judge cannot do that). Import: `lode.gate`.
  corpus/
    kb-01-storycraft/   # the first knowledge base (narratology / craft / worldbuilding)
    kb-02-…/            # more KBs — the engine runs per-KB via `-c <kb>/library.toml`
  eval/        # retrieval / token / benchmark harnesses for the engine
  examples/    # examples/gate_demo.py — the supervisor over kb-01
  dev-docs/    # the design record: the two-loop architecture, plans, reviews, SPEC.md
```

## The two loops

- **Loop A — encode expertise** (`lode.lib`): turn sources into cited, retrievable knowledge. Every
  card claim carries a verbatim `(grep: …)` anchor; `lode check` fails if any citation stops resolving.
- **Loop B — supervise work** (`lode.gate`): submit a draft, score it against criteria loaded from a
  KB's craft layer, and return **Go / Recycle** — each cited defect grounded through `lode.lib.verify`,
  so the judge's citations are un-fakeable.

The boundary is enforced: `lode.gate` consumes only the `lode.lib` public API (facade), never its
internals (`tests/test_layering.py` guards it).

## Quickstart

```bash
lode -c corpus/kb-01-storycraft/library.toml check          # validate a KB (citation-rot linter)
lode -c corpus/kb-01-storycraft/library.toml consult worldbuilding   # read a craft lens
python examples/gate_demo.py                                # supervise a draft against kb-01
python -m unittest discover -s tests                        # the full suite (153 tests)
```

## Status

- `lode.lib` — solid: 145 tests, a stable public-API facade, an AST layering guard, clean-venv wheel smoke.
- `lode.gate` — a **walking skeleton** (8 tests): the chain and the un-fakeable-citation mechanism are
  proven; the rubric **judge is a stub** (`FixtureJudge`) — the real LLM judge (G-Eval two-step,
  debiased, calibrated against a human gold set) plugs into the `Judge` protocol. Calibration is the
  next real build. See `dev-docs/`.
