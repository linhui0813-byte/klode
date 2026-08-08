# eval — how to test klode's efficiency

"Efficiency" splits three ways for a knowledge library. Only one (retrieval) is about whether the
tool *works*; the other two are cost. All three are stdlib-only and take `-c path/to/library.toml`.

| harness | question | run |
|---|---|---|
| `retrieval.py` | does `search` surface the **right** sources? (efficacy) | `python3 eval/retrieval.py -c LIB -v` |
| `tokens.py` | does "pull the cheapest layer" **pay**? (token cost) | `python3 eval/tokens.py -c LIB` |
| `bench.py` | how **fast**, and how does it grow? (speed/scaling) | `python3 eval/bench.py -c LIB` |
| `rate.py` | does a **rubric** mean the same thing to two people? (Loop B) | `python3 eval/rate.py score a.jsonl b.jsonl` |

`rate.py` is the odd one out: it measures a `CriterionSpec`, not the library. It reports
quadratic-weighted kappa per criterion between two raters, because a rubric two people apply
differently is under-specified — and the fix is the criterion, not the rater. See
[`../dev-docs/SPEC-criterion.md`](../dev-docs/SPEC-criterion.md).

## retrieval.py — the one that matters

A search returning in 19 ms but ranking the wrong card is useless, so this is the real test.
`retrieval.jsonl` is the gold set: each line a question `q` + the hand-labelled `relevant` card
ids. The scorer runs each question through the shipped BM25 ranker and reports mean **P@5, R@10,
MRR, nDCG@10**, A/B'd against the pre-BM25 raw-count ranking and the `--full` (L2-included) variant.

**Grow the gold set** — it's the only part that needs your domain judgment. With incomplete gold the
scores are a *lower bound* (an unlabelled-but-relevant card counts as a miss), but the ranker A/B is
robust regardless. Add lines to `retrieval.jsonl` and re-run.

## Baseline — 2026-07-24, doxai corpus (129 cards, 46 MB source, 1903 anchors)

**`retrieval.jsonl` is labelled against that corpus**, which is private and not in this
repo. Point `-c` at it, or pass `--gold` with a set labelled for your KB; running it
against another library now exits with that message rather than reporting 0.000.

### Retrieval efficacy (16 seed questions)
| ranker | P@5 | R@10 | MRR | nDCG@10 |
|---|---|---|---|---|
| raw-count (pre-P4) | 0.375 | 0.958 | 0.922 | 0.898 |
| **BM25 (L0/L1, shipped)** | **0.400** | **0.979** | **1.000** | **0.962** |
| BM25 +Full (L2) | 0.400 | 0.969 | 0.958 | 0.936 |

Findings: BM25 beats raw-count on every metric (validates P4). MRR = 1.0 — the first relevant card
is always rank 1. Searching **L0/L1 beats +Full**: L2 text adds noise, so the default is right.
Residual recall gaps are only in dense clusters (suspense-generation, unawareness) — a vocabulary
problem the `aliases:` field patches, **not** a reason to add FTS5 or a vector DB (answers the open
question in `dev-docs/knowledge-base-research-conclusion.md` §3.5).

### Token / LOD efficiency (est. tokens @ chars/4)
| level | avg/card |
|---|---|
| L0 meta | 88 |
| L1 thin | 182 |
| L2 full | 938 |
| L3 source | 89,063 |

- Answering from **L1 instead of L3 ≈ 489× fewer tokens**.
- The whole browsable board (L0+L1, 129 cards) ≈ **34k tokens** — fits one context window — vs the
  full corpus L3 ≈ 11.5M tokens: **340× smaller**. This is the LOD payoff, quantified.

### Speed + scaling
| op | time | note |
|---|---|---|
| `lib check` | ~0.87 s | **bytes-bound**: normalizes 46 MB of source every run (was ~1.35 s before the `_norm` fix below) |
| `lib search` | ~13 ms | BM25 over 129 cards |
| `lib build` | ~86 ms | rewrite 129 cards + INDEX |

Synthetic sweep — **linear** in card count (check holds ~0.1 ms/card from 100→5000 cards; search
~linear, 135 ms at 5000). check's cost tracks total *source bytes*, not card count.

**Optimization applied (measured, not guessed):** `_norm` was the hotspot (~59% of check). The real
cost was the `re.sub(r"\s+"," ")` whitespace collapse, not the smart-quote folding — replacing it
with `" ".join(s.split())` is **~2× faster** over a full corpus (1030 ms → 504 ms on 46 MB), cutting
`lib check` ~35%. A `str.translate` fold was tried and *rejected* — it was slower (per-char hash
lookup beats near-free `.replace` for rare chars). Next lever, if ever needed: cache normalized
haystacks keyed by the P3 `source_sha256`, so unchanged sources aren't re-normalized at all.
