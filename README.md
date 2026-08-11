# klode

A **klode** — a rich vein — of grounded, verifiable knowledge, with the machinery to encode it and to
*supervise work* against it. Every claim is anchored to a verbatim source and checked by a fail-closed
linter, so a citation can't quietly rot: **cite, don't recall.**

That guarantee is **referential, not semantic** — it proves the quoted text is still there, in a
current source, in exactly one place. It does *not* prove the quote supports the claim built on it.
Green `klode check` means "no citation rot," never "this is true."

A second, narrower limit sits beside it. `klode ingest` verifies an extraction against a control and
against the rendered page, which catches dropped pages, duplicated blocks, and scrambled reading
order — but **no signal detects a value transposition** that preserves the words, the length, and
the order. A table extractor that swaps two cells produces this, and every check passes:

```
control:   Alice status approved   Bob status rejected
candidate: Alice status rejected   Bob status approved
```

Both claims are inverted; containment, inflation, order, and coverage are all perfect. **A
table-derived claim needs human verification.** Recorded rather than hoped away.

Zero runtime dependencies — Python 3.11+, standard library only.

## The two loops

- **Loop A — encode expertise** (`klode.lib`): turn sources into cited, retrievable knowledge. Every
  card claim carries a verbatim `(grep: …)` anchor; `klode check` fails if any citation stops resolving.
- **Loop B — supervise work** (`klode.gate`): submit a draft, score it against a **CriterionSpec** —
  an authored, corpus-pinned, human-approved rubric with stable ids and behaviorally anchored levels
  — and return **Go / Recycle**. Each cited defect is grounded through `klode.lib.verify`, so the
  judge's citations are un-fakeable, and every authored field declares whether it is the source's
  words, a paraphrase, an inference, or *not stated at all*. The boundary is enforced: `klode.gate`
  consumes only the `klode.lib` public API, never its internals.
  See [`dev-docs/SPEC-criterion.md`](dev-docs/SPEC-criterion.md).

## Install

```bash
pip install klode
# or from a source checkout:
pipx install -e .              # provides the `klode` and `klode-mcp` commands
```

## Three surfaces over one engine

A knowledge base is a `library.toml` plus a corpus of cited cards. Point klode at one with `-c`, or
register several in a manifest and address them by id (`--kb <id>` / `--registry`).

**CLI** — `klode`:

```bash
klode -c path/to/library.toml check                 # citation-rot linter (exit 1 on any broken citation)
klode -c path/to/library.toml search pacing         # retrieval over the cards
klode -c path/to/library.toml consult brevity       # read a craft lens
klode -c path/to/library.toml verify brevity "the exact quote"   # prove a quote against its source
klode ingest paper.pdf --shelf papers               # ingest a source -> clean, grep-ready text
klode kbs                                            # list the KBs in a registry
```

Add `--json` to any read verb for machine-readable output (the same structured result the MCP renders).

**MCP server** — `klode-mcp` (stdio): exposes the read/verify surface to an agent — `list_kbs`,
`search_sources`, `consult_dimension`, `consult_framework`, `zoom_card`, `verify_quote`, `diagnose`,
`list_lenses`. Serves one KB (`-c`) or many (`--registry`), and every grounded result names its KB.

**Library** — `import klode.lib`: the stable public-API facade — `verify`, `search`, `consult`,
`resolve`, `diagnose`, `Config`. Cheap to import; pulls in no frontends or optional backends.

## Ingestion — any format to grep-ready text

`klode ingest` detects the format by content signature (not the extension) and converges every source
on one clean-text pipeline: **PDF, EPUB, DOCX, HTML/XHTML, TXT**. EPUB/DOCX/HTML/TXT are pure stdlib;
only PDF's OCR tiers are optional.

For table-heavy or multi-column PDFs, point klode at a
[docling-serve](https://github.com/docling-project/docling-serve) endpoint — the GPU runs
server-side, so klode stays dependency-free — and pass `--tier docling`:

```toml
# ~/.klode/settings.toml
[ingest]
docling_url = "http://<host>:15001"
tier = "docling"          # or leave at "auto" and escalate only when the text layer is bad
```

`KLODE_DOCLING_URL` overrides the file, and `klode settings` prints every value with the source
that won. The URL is topology, not a credential: **bind docling-serve to a private interface**, and
do not rely on the URL being unguessable.

[marker](https://github.com/datalab-to/marker) is supported the same way (`marker_url`, `--tier
marker`) and is **remote-only** — it pulls torch and layout models, which klode does not depend on.
It is deliberately *not* in the `auto` escalation ladder: a backend earns a ladder slot by
measuring better than the one it would displace, not by being installed.

Backends are chosen by measurement, never by intuition — `eval/extract_bakeoff.py` ranks them
against the *rendered page* (`pdftoppm` + `tesseract`), the one signal not downstream of another
extractor. On the fixture corpus, docling and `pdftotext` tie on recall while docling is the only
one that reads a two-column page in the right order; that gap is what the reading-order column
exists to show.

## Architecture

One operations registry projects a single core to both surfaces (CLI + MCP), so they can differ in
formatting but never in behaviour. Every grounded result carries structured provenance (which KB,
which source, which policy). See `dev-docs/` for the design record and `SPEC-operations.md` for the
machine-readable operation table.

## Status

`0.3.0` — beta. The engine (`klode.lib`) is solid: **600 tests**, a stable public-API facade, an AST
layering guard, a content-sniffing multi-format ingester, and an MCP server, all with zero runtime
dependencies. `klode.gate` (Loop B) is now a fail-closed supervising gate — freshness/review-aware
grounding (`verify_evidence`), a bounded evidence-context op (`verify_context`), a fail-closed
verified-context bundle (`build_context_bundle`), a shared structured anchor contract
(`parse_markers`/`Marker`, regex/context/`#n`), and **CriterionSpec v1** as its sole rubric input
(field-level epistemics, behaviorally anchored levels, a computed corpus fingerprint, and a
human-approval admission gate) are in place, and the rubric judge is **real** (`LLMJudge`:
G-Eval two-step form-filling, position-bias-debiased by balanced permutation, transport-injectable,
stdlib-only). What is **not** done is *calibration*: no rubric has yet been measured against human
scores, so `Verdict.calibrated` is False and every verdict is marked non-production — by mechanism,
not by convention. The optional entailment check
(`klode check --entail`) pulls a small NLI model behind `klode[entail]` and is advisory, warn-only.

## License

MIT — see [LICENSE](LICENSE).
