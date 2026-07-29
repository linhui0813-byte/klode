# lodlib card format & disciplines

This is the contract. A library is a directory tree; a **card** is one Markdown file per source.
The format is deliberately plain — human-readable, greppable, diffable, and machine-checkable — so
that no part of the library depends on a running service to be understood.

## Directory layout

```
library.toml                 # configuration (the only knob)
library/                     # [library].dir
  <shelf>/                   # one dir per [library].shelves entry — holds source .txt files
    <id>.txt                 #   the raw source (L3) — usually git-ignored (copyright)
  cards/                     # [library].cards
    <id>.md                  #   one card per source
    INDEX.md                 #   the generated board
  BIBLIOGRAPHY.md            # optional catalog ([bibliography])
  frameworks/                # optional per-dimension distillations ([frameworks], off by default)
    _syntheses/              #   optional best-of-breed adjudications
```

The card `id` is the source filename stem, so `library/books/aristotle-poetics.txt` ⇒ card
`library/cards/aristotle-poetics.md`. That bijection is enforced by `lib check`.

## The card

```markdown
---
id: aristotle-poetics
shelf: books
file: library/books/aristotle-poetics.txt
framework: none
zoom: full
aliases: [mimesis, catharsis, peripeteia, recognition]
grep_ready: true
---

# Aristotle — Poetics (tr. S. H. Butcher)

**Bibliography.** <the catalog row, mirrored from BIBLIOGRAPHY.md>

## Content
`library/books/aristotle-poetics.txt` — full text (git-ignored, grep-ready). Never duplicated
here; grep it to verify.

<!-- scaffold: managed by lodlib — edit Thin/Full below the marker by hand -->

## Thin
<L1: 1–3 sentences, the source's core engine, each load-bearing claim `(grep: "…")`-anchored>

## Full
<L2: main points outlined, every claim `(grep: "…")`-anchored>
```

**Above the scaffold marker is machine-managed** (`lib build` writes it). **Below the marker is
yours** — `lib build` never touches it, so a rebuild is a safe no-op on your prose.

### Front-matter fields

| field | meaning |
|-------|---------|
| `id` | source filename stem (== card filename stem) |
| `shelf` | which `[library].shelves` dir the source lives on |
| `file` | path to the source `.txt`, relative to the library root |
| `framework` | `none`, or a path to a per-dimension distillation (optional layer) |
| `zoom` | how far the card is filled: `stub` (L0) → `thin` (L0+L1) → `full` (L0+L1+L2) |
| `aliases` | `[term, …]` concept synonyms, to widen `lib search` recall — hand-filled, never invented |
| `grep_ready` | the source has been normalized for grep (see `lib normalize`) |
| `source_sha256` | *(optional, freshness)* hash of the source when claims were last verified — set by `lib build --stamp`; `lib check` warns when the live source no longer matches |
| `review_by` | *(optional, freshness)* an ISO date; `lib check` warns once it has passed — re-review |
| `superseded_by` | *(optional, freshness)* a newer source's id; `lib check` warns so references are re-pointed |

## The two disciplines (non-negotiable — they keep it from rotting)

1. **Cite, don't recall.** Every L1/L2 claim is grep-grounded to the source: a quoted phrase plus a
   `(grep: "search string")` anchor. A summary written from memory is drift wearing a citation. Two
   anchor conventions are accepted and checked:
   - `` (grep: `phrase`) `` — backtick-delimited
   - `` (`grep: "phrase"`) `` — whole marker backticked, phrase double-quoted

   `lib check` normalizes before matching (folds whitespace, hyphenation, and smart quotes), so a
   phrase wrapped across extracted lines still resolves. An anchor that no longer resolves is a
   **citation-rot ERROR**.

   **Anchors are matched LITERALLY.** A phrase is never silently retried as a regex — that was a
   fail-open in the one check that must never fail open. For a deliberate regex (a `.*` spanning a
   gap, a `.` for a variable punctuation char), opt in explicitly with `grep-re:` /
   `search-re:` — everything else is verbatim.

   **Optional disambiguation** pins *which* occurrence must resolve, so a quote that rotted at its
   intended spot is caught even when a coincidental copy survives (W3C TextQuoteSelector /
   text-fragment style):
   - `` (grep: `phrase` before `preceding words` after `following words`) `` — prefix/suffix context
   - `` (grep: `phrase` #2) `` — the 2nd occurrence

   `lib check --strict` additionally WARNs when a bare anchor resolves in more than one place (add
   `before:`/`after:`/`#n` to pin it).

   **Several anchors can share one marker**, `;`/`|`-separated. Each phrase is checked:
   - `` (grep: `A`; `B` | `C`) `` — `B`/`C` are bare phrases (they inherit `grep`'s literal/regex type)
   - `` (`grep: "A"`; `grep: "B"`) `` — each phrase re-states the key; both are full anchors

2. **Stub cheap, fill on demand.** Every source gets a card (L0) the moment it lands; L1/L2 are
   written only when the source is actually used. `lib build` **never invents** a summary — it only
   scaffolds L0 and marks L1/L2 *owed*. `zoom:` records how far each card is filled.

## What `lib check` enforces

| id | check | severity |
|----|-------|----------|
| A | every card's `file:` points to a real shelf source | ERROR |
| B | every shelf source has a card | ERROR |
| C | every shelf source appears in the bibliography | WARN |
| D | `INDEX.md` lists exactly the cards on disk | ERROR |
| E | no guarded `.txt`/`.pdf` is git-tracked (copyright leak) | ERROR |
| F | every `(grep: …)` anchor still resolves in its source (literal; `grep-re:` for regex) | ERROR |
| G | each card's mirrored bibliography line matches the live catalog | WARN |
| H | freshness: `source_sha256` still matches / `review_by` not lapsed / not `superseded_by` | WARN |

When the corpus is absent (a fresh clone where the git-ignored sources aren't installed), the
source-dependent checks (A per-card, B, F, and the H hash) are skipped rather than failing — the
tracked-file checks (D, E) still run. Outside a git work tree, the copyright guard (E) reports N/A
rather than erroring, since there is nothing to leak into.

**A separate, opt-in semantic pass.** `lib check --entail` (needs the `lodlib[entail]` extra) scores,
for each anchor, whether the source *window* actually supports the card's claim — a small, pinned NLI
model (SummaC-style windowing). It is **warn-only, never a gate**: the automatic-attribution accuracy
ceiling is ~77–80%, so a low score is a review prompt, not a failure. Grep resolution stays the only
hard gate, and the default (no-extra) path stays zero-dependency.

## Adding a source

1. Drop the `.txt` on its shelf; add a `BIBLIOGRAPHY.md` row.
2. `lib normalize --apply` (if it's a pdftotext/ebook dump) to make it grep-ready.
3. `lib build` — scaffolds the card (L0) and refreshes the board.
4. Write L1 (and L2 if you're mining the source), grep-anchored, below the scaffold marker; bump
   `zoom:`.
5. `lib check` — must pass.

## The synthesis craft layer (the `_syntheses` contract)

A dimension synthesis serves more than one reader: a **writer/editor** wants the moves, a
downstream **engine** wants the scorer mapping, an **auditor** wants the provenance. Fusing all three
into one body forces every reader to project by hand — a regression from the "pull the cheapest layer
that answers" promise. So a synthesis SHOULD open with a self-contained writer layer:

- `## Craft` — the writer-actionable payload: the **mechanism**, the **moves** (what to DO), and the
  **live options** to choose among. It must **stand alone and be engine-free** — no `scorers.*`,
  `spec/…`, `RT-…`, ε-test, or gate references. Every load-bearing claim carries its **verbatim**
  `(grep: …)` anchor (the same string used elsewhere in the document — gathered, never re-summarized,
  so it cannot rot). The one-line `**Core question:**` and `**Status/Tier:**` stay above it; both
  audiences need them.
- The remaining engine mapping (`Operational spec for the engine`, scorer knobs) and audit apparatus
  (ε-test / falsification designs, `Owed`, the skeptic-gate row, promotion history) keep their
  existing sections below.

`lib consult <dimension>` returns the `## Craft` section by default (falling back to a section menu
when a synthesis has none yet); `--full` returns the whole document; `--section engine` / `--section
provenance` (or any heading keyword) select the deeper layers. The MCP `consult_dimension` mirrors
this (`audience=writer` default). This is the same level-of-zoom discipline the source cards use,
applied one tier up.

**Resolving a name.** `lib consult <name>` resolves a dimension/framework stem, an author, or a book
title. Dimensions are matched by **exact stem or family prefix only** (`viewpoint` →
`viewpoint-who` / `viewpoint-showing`); only frameworks and source cards additionally fuzzy-match on
title + aliases. Dimensions are **deliberately kept out of the fuzzy token index** — a generic word in
one synthesis's haystack must never silently outrank the right lens. A dimension's informal or
historical names (e.g. an old name kept after a rename) therefore live in
`_syntheses/_diagnostics.md`, the hand-editable `words → dimensions` table that `lib diagnose` reads;
that is how a legacy term like `presence` reaches its current dimension (`reader-immersion`). There is
intentionally no `aliases:` field on a synthesis.

## Public API and internal layering

`import lodlib` exposes a small, stable **Loop-A contract** — `Config`, `consult` /
`ConsultRequest` / `ConsultResult`, `resolve` / `Resolution`, `verify` / `Verification`, `dimension`,
`framework`, `diagnose`, `search`. A consumer (e.g. a supervising "Loop B" agent) imports from
`lodlib`, not from submodules; submodules are internal and may be refactored. Importing `lodlib` is
cheap and pulls in **no** frontends or optional/heavy deps (`cli`, `mcp_server`, `entail`, `ingest`,
OCR). The citation-rot linter is a maintenance tool, reached at `lodlib.check.check` or the `lib check`
CLI — deliberately not re-exported (the name would shadow the `lodlib.check` module).

The package is **flat** by design (no `core/services/adapters` nesting — it would add no mechanism at
this size and would break `packages=["lodlib"]`). The one boundary that carries a mechanism is
enforced by `tests/test_layering.py`: the two frontends (`cli`, `mcp_server`) are **adapters** —
nothing depends on them, they don't depend on each other, and both route consult through the shared
**`console`** layer. `console` owns *what* to return (resolution + lens loading + section selection);
the adapters own *how* to present it and their protocol semantics (CLI exit codes, MCP redirect and
missing-section fallback). This is the structural guard against the CLI/MCP drift that the parity
report §6 documented.
