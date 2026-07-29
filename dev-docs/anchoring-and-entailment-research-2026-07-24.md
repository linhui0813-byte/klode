# Research + plan: anchoring robustness & the entailment gap

**Date:** 2026-07-24
**Scope:** A fresh external scan of the problem lodlib solves, an honest re-evaluation of the current
solution against the 2025–2026 state of the art, and a decision — *improve ours* vs *adopt a better
one* — with a prioritized, ethos-preserving roadmap (P0–P4). Supersedes nothing in
[`knowledge-base-research-conclusion.md`](knowledge-base-research-conclusion.md) (2026-07-21); it
extends it with the anchoring + entailment findings and folds in the two verified defects' current
status.

This is the auditable record for the change that follows. Two background research agents (web + primary
docs) produced the external landscape; their load-bearing sources are listed at the end.

---

## 1. The problem, stated plainly

lodlib solves **citation rot** — the silent drift between a summary and the source it claims to quote.
When you build a knowledge base over sources you can't read whole or paste wholesale (books, papers,
transcripts), the classic failure is: you write "X argues Y," the source is re-OCR'd or you misremember,
and the claim quietly becomes *fiction wearing a citation*. Most notes/RAG systems let that happen
silently. lodlib makes it **loud**, under three simultaneous constraints:

| Constraint | Mechanism | One line |
|---|---|---|
| **Verifiable grounding** | every L1/L2 claim carries a `(grep: "exact phrase")` anchor; `lib check` exits 1 when an anchor no longer resolves | "cite, don't recall" |
| **Copyright-safe by construction** | source `.txt` always git-ignored; only derived cards tracked; a tracked corpus file is an ERROR | safe by construction |
| **Level-of-zoom + agent-readable** | L0 meta / L1 thin / L2 full / L3 raw; pull the cheapest layer that answers; 6 tools over MCP | zoom on demand |

The unit of organization is the **source** (one card per source, bijection enforced by `lib check`). It
solves "what each source *verifiably says*" well; it does not yet express "what *you* conclude *across*
sources" (the Tier-2 synthesis layer — `_syntheses/` in the doxai corpus is its prototype).

---

## 2. Honest self-evaluation (grounded in the current code, not the old doc's claims)

### What it genuinely gets right (first-principles)

- **Governance is the bottleneck, not the model.** The enforced one-card-per-source bijection is
  precisely the governance enterprises fail at (governed RAG 85–92% accuracy vs ungoverned 45–60%).
  lodlib bakes governance into the linter rather than trusting discipline.
- **Deterministic, zero-dependency, offline, git-diffable.** The library *is* a directory of Markdown —
  readable, greppable, diffable, committable — with no running service required to be understood. This
  is a structural advantage over every SaaS / vector-DB competitor.
- **Its two refusals are features:** no auto-summarization, no vector index. Both are the anti-drift core.

### Weaknesses (named honestly; verified against current source)

| # | Weakness | Severity | Current status in code |
|---|---|---|---|
| 1 | **grep proves the anchor *resolves*, not that the quote *entails* the claim** | design-level, largest semantic gap | green `check` = "no citation rot", never "true"; harmless at Tier 1 (claim ≈ quote), widens on synthesis |
| 2 | **regex fail-open** in `common.py::occurs()` (`re.search` fallback, lines 108–111) | HIGH — in the one check that must never fail open | **still live**; a literal quote whose punctuation is a regex metachar can *false-pass*. Fix scoped (opt-in `grep-re:`), not yet done |
| 3 | **quote-only anchor, no prefix/suffix context** | MEDIUM | a phrase occurring twice matches ambiguously; can't pin "which occurrence" |
| 4 | **no freshness / supersession** | MEDIUM (bites at scale) | citation-rot ≠ knowledge staleness; stale sources silently keep high trust |
| 5 | **retrieval is naïve term-frequency** (`query.py:69` `sum(blob.count(t))`) | LOW | no length normalization / IDF → biases toward the longest card; unmeasured beyond a few hundred sources |

Note: the *other* verified defect from the 2026-07-21 doc — one-directional de-hyphenation — is **fixed**
(`common.py::_dehyphenate` now folds `-\s*` on both source and needle).

---

## 3. External landscape (2025–2026): is there a better solution to adopt?

### 3.1 The decisive external fact

> **"Deterministically verify, in CI, that a claimed quote still appears verbatim in a specific *local*
> source" has no off-the-shelf equivalent.** Everything shipping falls in an adjacent-but-different box.

| Direction | Representative | Guarantees | Missing (vs us) |
|---|---|---|---|
| Link/anchor validity | **lychee** (incl. `#:~:text=` fragment checking) | a link/fragment currently resolves | not "that passage still *says* this" |
| Reference-identifier validity | **RefChecker**, **refcheck** (MCP), Citation-Verifier | DOI/arXiv/URL resolves; detects hallucinated citations | identifier only, not verbatim body text |
| Extraction-time offset grounding | Google **LangExtract** (OSS) | every extraction carries `start/end` char offsets + a null-interval hallucination filter | extraction library, not a KB/linter; runs an LLM per pass |
| Model-judged faithfulness | RAGAS / TruLens / DeepEval | probabilistic "answer faithful to context?" | non-deterministic, non-verbatim, needs an LLM |

### 3.2 Closest three comparables — each wins one or two axes, drops the rest

| Comparable | Better than us | Worse than us |
|---|---|---|
| **Obsidian + broken-link plugins + Zotero/BibTeX + Obsidian-MCP** | mature local Markdown ecosystem, graph, best privacy posture, many MCP servers, real git workflow | link check is *internal wikilinks only*; **zero verbatim quote↔source verification**; no citation-rot CI — the KB substrate, not the fidelity guarantee |
| **NotebookLM / Gemini Notebook** | polished source-grounded Q&A at scale, multi-doc synthesis | **cloud** (sources leave your machine → copyright/privacy), not git-diffable, no open API/MCP, ~13% hallucination, no deterministic anchor, no CI lint |
| **LangExtract** (Google OSS) | the only mainstream tool whose grounding is **mechanical** (exact char offsets + hallucination filter), runs local via Ollama, JSONL diffable | an extraction library, not a browsable KB or linter; needs an LLM per pass; not MCP-native |

**Net: no single product is the union of** grep-deterministic fidelity + git-diffable + citation-rot-linted
+ MCP-exposed + local. That union is the defensible gap lodlib occupies. **⇒ There is nothing better to
adopt wholesale. The decision is to improve ours.**

### 3.3 The three mechanism tiers for the entailment gap (weakness #1)

| Tier | Mechanism | Determinism | Local/CPU | Accuracy ceiling |
|---|---|---|---|---|
| 1 (today) | string/lexical resolve — proves the anchor *exists* | fully deterministic, zero-dep | yes | n/a (doesn't check meaning) |
| 2 | **NLI entailment models** (MiniCheck-FT5 770M, AlignScore 355M, SummaC) | reproducible under greedy/argmax decoding | **yes** (encoder models, CPU-real-time on a windowed span) | ~77–80% balanced-acc |
| 3 | LLM-as-judge (RAGAS, TruLens, DeepEval, Vertex check-grounding) | non-deterministic | needs a local LLM (heavy) | highest nuance, still bounded |

Key discipline: the automatic-attribution accuracy ceiling is **~77–80%** (AttributionBench;
LLM-AggreFact). **An imperfect metric that hard-fails CI produces false failures.** Therefore any
entailment layer must be **advisory (warn-only), never a hard gate** — grep resolve stays the gate.

Architectural principle to borrow (Anthropic/Cohere citations): **separate the two guarantees** — a
*deterministic, system-verified pointer* (our grep resolve; the offsets Anthropic computes) vs a
*best-effort semantic support* claim. Surface them as two differently-trusted fields; never collapse them.

Cost-cutter to borrow (SummaC): whole-document NLI is unreliable; **sentence/window-granularity NLI**
recovers accuracy *and* is far cheaper. The grep anchor already localizes the span — feed the NLI model
only the tight window around the resolved anchor, not the whole `.txt`.

Precision idea to borrow (ALCE): check both **sufficiency** ("does the window entail the claim?") and
**necessity** (leave-one-out — is this window actually the right one, or a coincidental phrase match?).

### 3.4 Anchoring robustness (weakness #3) — cheap, deterministic, no new deps

| Idea (source) | Fixes | Cost |
|---|---|---|
| **prefix + suffix context** on each anchor (W3C TextQuoteSelector; text-fragment `prefix-,…,-suffix`; Hypothesis' 32-char windows) | duplicate-phrase ambiguity, deterministically | **low** — two extra normalized strings; matcher stays substring-scan + neighbor check |
| **occurrence index / `textEnd` range** (text fragments deliberately omit an index that grep can add cheaply) | residual case where neighbors are *also* identical | **very low** (index — grep already enumerates matches) |
| **fuzzy re-anchoring** (Hypothesis diff-match-patch/Bitap) | anchor survives light re-extraction/edits | **medium + a real perf footgun** ("short generic quote × long doc ≈ hang") — only worth it if sources are re-extracted; our sources are fixed books ⇒ skip |
| **content hash + CI drift flag** | CI *reports* that a source changed instead of silently rotting | **low** (hash + compare) |

---

## 4. Decision & roadmap: improve ours (not adopt) — P0–P4

**Verdict: improve ours.** No better solution exists to adopt; the research hands over a precise,
ethos-preserving roadmap. Governing principle across all of it: **keep grep as the only hard gate**
(deterministic, zero-dep, offline); everything probabilistic is opt-in and warn-only; the default path
never gains a dependency.

| P | Change | Fixes | Mechanism (borrowed) | Cost | Ethos impact |
|---|---|---|---|---|---|
| **P0** | regex fail-open → opt-in `grep-re:`; literal by default | #2 | frontier consensus: the deterministic gate must be literal | ~small; migrate the few intentional-regex anchors | **strengthens** — removes a fail-open; still zero-dep, deterministic |
| **P1** | prefix/suffix context (+ occurrence index) on anchors; `--strict` flags ambiguous bare anchors | #3 | W3C TextQuoteSelector; URL text fragments; Hypothesis | **low** — two extra normalized strings; reuse existing normalization; backward-compatible | **fully preserved** — zero-dep, deterministic |
| **P2** | optional entailment layer `lib check --entail` behind an `[entail]` extra | #1 | MiniCheck-FT5 / AlignScore (local, greedy=reproducible); SummaC windowing; ALCE sufficiency+necessity; **warn-only** | heavy dep **only on the opt-in path**; default stays zero-dep | default zero-dep unchanged; pinned model ⇒ reproducible *warnings*, never a CI gate |
| **P3** | supersession/review dates + source content hash | #4 | dev-doc §4.1#4; robust-links content hash | low — front-matter fields + hash compare | zero-dep, deterministic |
| **P4** | length-normalized retrieval (BM25-lite, pure stdlib) | #5 | classic IR; measure before adopting SQLite FTS5 | low | stdlib only; no vector DB |

### What NOT to build (independent judgment)

- **Vector DB / embeddings** — re-rejected; no fidelity guarantee uses them; they reintroduce the drift
  the design removes.
- **Fuzzy re-anchoring** (diff-match-patch/Bitap) — performance landmine; only if sources are
  re-extracted/edited. Our sources are fixed books ⇒ skip (gate hard by length/uniqueness if ever added).
- **LLM-as-judge entailment** (RAGAS/TruLens) — non-deterministic + heavy; encoder-NLI (MiniCheck /
  AlignScore) is strictly better for this ethos.
- **Cloud KB** (NotebookLM) — violates copyright-safe-by-construction.

---

## 5. Sources (load-bearing)

**Anchoring / rot:** [W3C Web Annotation Data Model](https://www.w3.org/TR/annotation-model/) ·
[URL Text Fragments spec](https://wicg.github.io/scroll-to-text-fragment/) ·
[Hypothes.is fuzzy anchoring](https://web.hypothes.is/blog/fuzzy-anchoring/) ·
[hypothesis/client #3919 (perf)](https://github.com/hypothesis/client/issues/3919) ·
[robertknight/anchor-quote](https://github.com/robertknight/anchor-quote) ·
[lychee](https://lychee.cli.rs/) · [Robust Links](https://robustlinks.mementoweb.org/spec/) ·
[Perma.cc](https://perma.cc/)

**Competitors / KB:** [NotebookLM](https://notebooklm.google/) ·
[LangExtract](https://github.com/google/langextract) ·
[Obsidian broken-links plugin](https://community.obsidian.md/plugins/broken-links) ·
[mcp-obsidian](https://github.com/Piotr1215/mcp-obsidian) ·
[Better BibTeX](https://retorque.re/zotero-better-bibtex/) ·
[refcheck (MCP)](https://github.com/benchoi93/refcheck) ·
[RefChecker](https://github.com/markrussinovich/refchecker)

**Entailment / faithfulness:** [MiniCheck](https://github.com/Liyan06/MiniCheck)
([paper](https://arxiv.org/abs/2404.10774)) · [AlignScore](https://github.com/yuh-zha/AlignScore)
([paper](https://arxiv.org/abs/2305.16739)) · [SummaC](https://arxiv.org/abs/2111.09525) ·
[FactScore](https://github.com/shmsw25/factscore) · [ALCE](https://github.com/princeton-nlp/ALCE)
([paper](https://arxiv.org/abs/2305.14627)) ·
[AttributionBench](https://osu-nlp-group.github.io/AttributionBench/) ·
[LLM-AggreFact leaderboard](https://llm-aggrefact.github.io/blog) ·
[Anthropic Citations API](https://www.anthropic.com/news/introducing-citations-api) ·
[RAGAS faithfulness](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/) ·
[TruLens RAG Triad](https://www.trulens.org/getting_started/core_concepts/rag_triad/) ·
[DeepEval faithfulness](https://deepeval.com/docs/metrics-faithfulness) ·
[Contextual AI GLM](https://contextual.ai/blog/introducing-grounded-language-model)

**Second opinion of record:** two background research agents (web + primary docs), this session.

---

## 6. Implementation status (shipped 2026-07-24)

All five priorities landed on branch `feat/anchor-robustness-and-entailment`; 53 tests pass, `lib
check` stays 0/0 on both the `example/` library and the real 129-card doxai corpus.

| P | What shipped | Where |
|---|---|---|
| **P0** | matcher is **literal by default**; the silent `re.search` fallback is gone; regex opts in via `grep-re:`/`search-re:`. `Marker` type carries `regex`. | `common.py` (`parse_markers`, `resolve`), `check.py` |
| **P1** | `before:`/`after:` prefix/suffix context + `#n` occurrence index on anchors; `lib check --strict` warns on ambiguous bare anchors; the example's non-conforming anchor fixed | `common.py`, `check.py`, `cli.py` |
| **P2** | opt-in `lib check --entail` behind the `[entail]` extra: SummaC-window + pinned NLI backend (lazy import, graceful degrade), **warn-only**; default path stays zero-dep | new `entail.py`, `check.py`, `cli.py`, `pyproject.toml` |
| **P3** | freshness: `source_sha256` (stamped by `lib build --stamp`), `review_by`, `superseded_by`; `lib check` warns on drift/lapse/supersession | `build.py`, `check.py`, `cli.py` |
| **P4** | retrieval is **BM25** (IDF + length normalization, pure stdlib); substring tf kept | `query.py` |

### P0 migration outcome (corpus)

Removing the fail-open surfaced **4 anchors** in doxai that had been passing *only* via the regex
fallback. On inspection none needed regex — all four were authors dodging curly quotes with `.*`/`.`,
which the smart-quote folding already handles. They were rewritten as **clean literal anchors** (more
precise than `grep-re:`), and the corpus is green again. This is P0 working as intended: anchors dragged
out of the fail-open shadow turned out to be literal all along.

## 7. P0.5 — the multi-phrase marker hole (fixed 2026-07-24)

While migrating P0 a **separate, pre-existing** extraction gap surfaced: the corpus writes several
anchors in one marker, and `parse_markers` only captured the first. Closing it revealed the corpus uses
**two** styles — a distinction that mattered:

- `(grep: \`A\`; \`B\`)` — `B` is a **bare** phrase with no key of its own. This was the **real hole**:
  `B` was never checked. `parse_markers` now captures it (inheriting the primary's literal/regex type).
- `(\`grep: "A"\`; \`grep: "B"\`)` — each phrase **re-states the key**, so `_KEY_RE` already found each one
  independently. These were **never** unchecked — the naive `; \`…\`` count that first suggested "222
  unchecked phrases" was inflated by counting this already-checked style.

The true fallout, once `_MORE_RE` was given a negative-lookahead so it captures only genuinely-bare
seconds, was **2 anchors**, both migrated:
- `Ewens.*1972` → `Ewens (1972)` (a paren-dodge; literal resolves).
- `writers who want to take responsibility are wary of it` → `Writers …` (case: the source capitalises
  the sentence-initial "Writers"; the matcher is case-sensitive by design, so the anchor was aligned to
  the source rather than weakening the matcher).

Shipped: `_MORE_RE` in `common.py` (with the keyed-anchor lookahead), 5 parser tests, the SPEC multi-phrase
note, and the 2 corpus rewrites. doxai is green (0/0). Case-sensitivity of the matcher was left unchanged
— folding it globally would ripple across the whole corpus and is a separate decision, not a bug fix.
