# Modularization plan v2 — hardened against two adversarial reviews

> **✅ Executed 2026-07-28** (branch `refactor/console-facade` → `main`). P0 characterization net,
> P1 `console.py` (shared consult layer), P2 public-API facade, P3 AST layering guard + MCP schema
> tests + SPEC. **145 tests green, `lib check` 0/0, clean-venv wheel smoke (`python -m lodlib` / `lib`
> return 0).** The flat-package decision held: no module moved, so packaging was never at risk.

**Status:** plan / architecture decision (revised). **Date:** 2026-07-28.
**Supersedes** `modularization-plan-2026-07-28.md` (v1). Hardened against a self-grill and an
independent Codex inspection audit (`modularization-codex-review-2026-07-28.md`). Every load-bearing
claim below was verified against the actual code, not memory or either reviewer's word.

## What v1 got wrong (verified corrections)

| v1 claim | Reality (checked) |
|---|---|
| "Breaks the one import cycle (`query→build`)." | **No cycle exists.** `build.py` imports only `common`+`config`, never `query`. The `query`→`build` lazy import is one-directional. The move, if done, is for *ownership*, not cycle-breaking. |
| "Extract model helpers → `core/model.py` (a domain model)." | It's a **utility drawer**: `source_sha256` (fs hash), `framework_source_map` (file scan), `bib_line_for` (reads bib), `humanize` (display strings). Not a model. Don't create it to satisfy a diagram. |
| "`common.py` = the pure matcher." | `common.py` also holds `read`/`read_lenient`/`card_files`/`shelf_txts` — **filesystem I/O**, not pure domain. |
| "Move `cli`/`mcp_server` → `adapters/`." | **Would break the installed package.** `pyproject.toml` sets `packages=["lodlib"]` and `scripts` → `lodlib.cli:main` / `lodlib.mcp_server:main`; `__main__.py` imports `.cli`. New subpackages aren't in the wheel; `python3 -m lodlib` (an external contract — a sibling repo's pre-commit hook) and the `lib`/`lodlib-mcp` scripts break. Source-tree tests stay green while the wheel is unusable. |
| Move-map "exhaustive." | Omitted `sections`/`title`/`card_path`/`meta`/`body`/`card_title`/`Source`/`source_of` — the **card-access** concern used by `search`, `verify`, and both zoom paths. And `sections()` parses ordinary source cards too, so filing it under "lenses" mis-couples `search`/zoom to a lens module. |
| "Both frontends format the same `ConsultResult`; adapters hold zero policy." | **False.** CLI exits 1 on a missing section; MCP falls back to the full body. `consult_dimension` *redirects* a framework to `consult_framework`; CLI *dispatches* to it. MCP has `audience=writer/engine/full` + a section **array**; CLI has `--section` (one substring) + `--full`. These are legitimate protocol differences that must remain **adapter-owned**. |
| Golden snapshots ⇒ "behavior byte-identical." | Snapshots covered only consult/diagnose. `build`/`check`/`ingest`/`normalize`/`search`/zoom/MCP-JSON-RPC are unprotected; `lib check 0/0` tests one repo state, not the output/exit-code/filesystem matrix. |

## Revised thesis: right = the seams with mechanisms, not the taxonomy

Both reviews converge: **"choose right" is not "restructure more."** It is the moves whose mechanism
demonstrably beats the status quo, done rigorously — and *cutting* the ceremony:

- **KEEP (real mechanism):**
  1. **The shared consult application layer** — kills the CLI/MCP drift bug class (the §6 defect was a
     symptom); one home for every future verb. *This is the load-bearing change.*
  2. **A curated public-API facade** — the contract Loop B imports instead of internals.
- **CUT (ceremony without mechanism, per both reviews):**
  - `core/services/adapters` **directory nesting** — no mechanism at 3.3k LOC (the graph is already
    directional) *and* it breaks packaging. **Stay flat.**
  - The **4-way `query.py` split** — premature fragmentation; `lenses`+`resolve`+`diagnose`+`gate`
    are one tightly-coupled consult domain. Defer; split only on a demonstrated seam.
  - `core/model.py` and the "cycle-break" — based on a false premise.

Net: v2 is **leaner in structure than v1 and more rigorous in safety**. It is *not* the original
"cheap" cut — that skipped the console layer (its real gap). v2 = cheap's restraint **+** the console
layer **+** a real characterization net **+** a derived facade.

## Target (flat package — unchanged layout, two new modules)

```
lodlib/
  __init__.py      → the PUBLIC API FACADE (curated; keeps back-compat re-exports)
  config.py common.py build.py check.py ingest.py normalize.py entail.py   (unchanged locations)
  query.py         → unchanged locations; stays the cohesive read-operations module
  console.py  NEW  → the shared consult application service (resolution + loading + projection)
  cli.py mcp_server.py  → stay put (no packaging break); their consult/diagnose bodies call console
  __main__.py      (unchanged)
```

Deferred (do only when Loop B shows the seam, and behind the facade so it's invisible to consumers):
splitting `query.py` into `cards`/`search`/`consult`/`verify`; relocating `framework_source_map`.

### The console contract (specified — this is the part v1 lacked)

```python
# console.py  — shared by cli.py and mcp_server.py; holds resolution+loading+section-selection ONLY
@dataclass(frozen=True)
class ConsultRequest:
    name: str
    kind: str = "any"           # "any" (CLI consult) | "dimension" | "framework" (MCP-scoped)
    projection: str = "writer"  # "writer" | "engine" | "full" | "sections"
    sections: tuple[str, ...] = ()   # requested section keys (CLI --section / MCP section array)

@dataclass(frozen=True)
class ConsultResult:
    outcome: str                # "dimension"|"framework"|"source"|"ambiguous"|"none"
    payload: object | None      # typed DimensionView/FrameworkView/SourceView (resolved+loaded)
    selected: tuple             # ordered (heading, body) after projection
    missing: tuple              # requested sections not found
    candidates: tuple           # LensMatch[] for ambiguous/none
    tier: str = ""              # dimension status/tier
    gate: str | None = None     # gate row for full/engine
    note: str = ""              # the shared ambiguous/none message (query.resolve)
```

**Honest invariant (replaces the false "adapters have zero policy"):** *resolution, loading, and
section-selection are shared in `console`; transport status, redirect wording, exit codes, and
presentation remain adapter-owned.* Adapters are thin, not policy-free — they own protocol semantics
(CLI exit 1 vs MCP full-body fallback on a missing section; `consult_dimension` redirect vs CLI
dispatch; terminal columns vs markdown).

## Phases (reordered — safety net first)

Invariant every phase: **114 unit tests + the P0 characterization suite + `lib check 0/0` + the
installed-wheel smoke test** all green; behavior identical to the P0 baseline. Branch; commit per phase.

- **P0 — Characterization safety net (before touching anything).** Subprocess-level, not in-process
  `cli.main`, so it catches packaging/entry-point regressions:
  - `python -m lodlib` for every command; assert **stdout, stderr, exit-code separately**.
  - Mutating commands (`build`/`build --stamp`/no-corpus, `ingest`, `normalize` dry-run/`--apply`/`--check`)
    compared via a **normalized filesystem manifest** (paths + bytes + hashes); build run twice for idempotence.
  - `check` with `--quiet`/`--strict` + entail-absent degradation; `search`; all zoom levels + grep exit codes.
  - MCP: real JSON-RPC lines to `lodlib-mcp` → snapshot `initialize`, `tools/list`, representative tool
    calls, and error envelopes (`isError`).
  - **Installed-wheel smoke:** build a wheel, install into a clean venv, run `python3 -m lodlib -c <fx> check`,
    `lib -c <fx> check`, `lodlib-mcp -c <fx>` — the exact external contract.
  *Done when:* the suite passes on current `main`.
- **P1 — `console.py` (load-bearing).** Implement `ConsultRequest`/`ConsultResult` + the projection
  (writer/engine/full/sections) once, in `console`. Rewrite `cli.cmd_consult`/`cmd_diagnose` and
  `mcp._tool_consult_dimension`/`_tool_consult_framework`/`_tool_diagnose` to call `console` and then
  apply only their protocol-specific bits (exit codes, redirect, fallback, formatting). Add
  `tests/test_console.py`: both adapters, given the same `ConsultRequest`, receive the same
  `ConsultResult`; the parity §6 regression test still passes. *Done when:* neither adapter contains
  resolution/loading/section-selection logic; P0 outputs unchanged.
- **P2 — Public-API facade + import audit.** Curate `__init__.py` from **Loop B's actual needs**
  (the `review_draft` skeleton: `Config`; `verify`/`Verification`; `console.consult`; `dimension`/`framework`;
  `check`) with `__all__` + a docstring naming it the contract. **Keep** `lodlib.query` / `lodlib.common`
  / `lodlib.build` importable (eval/*.py and tests import them directly — verified) — the facade *adds*
  a surface, it doesn't remove the internal ones. Add `tests/test_public_api.py`: (a) a full Loop-A
  round-trip importing **only** `lodlib`; (b) importing `lodlib` does **not** eagerly import
  `entail`/OCR deps or a running MCP server. *Done when:* both pass.
- **P3 — Guards + external-API tests + docs.** `tests/test_layering.py` using the **`ast`** module
  (not regex — regex misses the lazy `from .build import …` pattern): assert `cli` and `mcp_server`
  don't import each other and both route consult/diagnose through `console`. Add MCP **schema** tests
  (tool names, `audience` enum, param names are external API). Update `SPEC.md` with the facade
  contract + the console invariant; mark this plan done. *Done when:* guards + schema tests pass.

Loop B then starts in its **own package** against `import lodlib`, with its own layer test asserting it
touches only the facade (Python can't enforce "only surface" from `__init__` — enforcement lives in
the consumer).

## Non-goals

- No `core/services/adapters` directories; no module relocations (they'd break packaging for no
  mechanism). Revisit only if module count grows enough to warrant it, *with* a packaging phase.
- No `core/model.py`; no "cycle-break" (there is no cycle).
- No `query.py` split now (premature); no removing `lodlib.query`/`common`/`build` import paths.
- No behavior changes — pure restructure; any behavior change is a separate, later, deliberately-tested PR.
- No Loop-B code in lodlib.

## Rollback & cost

- **Rollback:** per-phase commits; revert a phase with `git revert <sha>` (not `git reset` — safer once
  shared). Nothing merges to `main` until every phase + the wheel smoke are green.
- **Cost:** P0 (the characterization suite) and P1 (the console contract) are the real work and the
  only parts needing design; there is no large mechanical move any more. Estimate ~half a focused day
  for P0, ~half for P1, the rest small.

## What both reviews agree is sound

The curated facade before Loop B; a shared resolution/loading layer (justified by the demonstrated
CLI/MCP drift); per-phase commits; an AST-based layering guard. v2 keeps exactly these and drops the rest.

## Definition of done

`import lodlib` exposes a documented, minimal, back-compatible facade; consult/diagnose resolution +
loading + section-selection live once in `console` with adapters owning only protocol semantics; a
subprocess-level characterization suite + installed-wheel smoke prove behavior + packaging unchanged;
an AST guard + MCP schema tests hold the external surface; 114 + new tests green; `lib check 0/0`.
