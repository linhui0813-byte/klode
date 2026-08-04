# klode

A **klode** — a rich vein — of grounded, verifiable knowledge, with the machinery to encode it and to
*supervise work* against it. Every claim is anchored to a verbatim source and checked by a fail-closed
linter, so the system can't quietly drift or hallucinate: **cite, don't recall.**

Zero runtime dependencies — Python 3.11+, standard library only.

## The two loops

- **Loop A — encode expertise** (`klode.lib`): turn sources into cited, retrievable knowledge. Every
  card claim carries a verbatim `(grep: …)` anchor; `klode check` fails if any citation stops resolving.
- **Loop B — supervise work** (`klode.gate`): submit a draft, score it against criteria loaded from a
  knowledge base's craft layer, and return **Go / Recycle** — each cited defect grounded through
  `klode.lib.verify`, so the judge's citations are un-fakeable. The boundary is enforced: `klode.gate`
  consumes only the `klode.lib` public API, never its internals.

## Install

```bash
pip install klode              # once published
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
only PDF's OCR tiers are optional. For table-heavy or scanned PDFs, point `$KLODE_DOCLING_URL` at a
[docling-serve](https://github.com/docling-project/docling-serve) endpoint (the GPU runs server-side —
klode stays dependency-free) and pass `--tier docling`.

## Architecture

One operations registry projects a single core to both surfaces (CLI + MCP), so they can differ in
formatting but never in behaviour. Every grounded result carries structured provenance (which KB,
which source, which policy). See `dev-docs/` for the design record and `SPEC-operations.md` for the
machine-readable operation table.

## Status

`0.2.2` — beta. The engine (`klode.lib`) is solid: **490 tests**, a stable public-API facade, an AST
layering guard, a content-sniffing multi-format ingester, and an MCP server, all with zero runtime
dependencies. `klode.gate` (Loop B) is now a fail-closed supervising gate — freshness/review-aware
grounding (`verify_evidence`), a bounded evidence-context op (`verify_context`), a fail-closed
verified-context bundle (`build_context_bundle`), a shared structured anchor contract
(`parse_markers`/`Marker`, regex/context/`#n`), and an enriched, anchor-validated criterion schema
are in place — but the rubric **judge is still a stub**
(`FixtureJudge`); a real LLM judge plugs into the `Judge` protocol. The optional entailment check
(`klode check --entail`) pulls a small NLI model behind `klode[entail]` and is advisory, warn-only.

## License

MIT — see [LICENSE](LICENSE).
