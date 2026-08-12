# SPEC — lode operations (the canonical noun/verb contract)

This is the single source of truth for lode's **operations**: the nouns it addresses, the verbs it
performs, and how each verb PROJECTS onto the two frontends (the MCP server and the CLI). Both
adapters are generated/tested against the operation table below (`lode/lib/opspec.py` is its
executable form; `tests/test_spec_operations.py` and the parity test guard the agreement). This is
distinct from `SPEC.md`, which defines the card format — that contract is untouched here.

Design rule: **one core, two projections.** A verb's logic lives once, in a domain service over the
KB-agnostic engine (`query`/`console`/`pool`). Each adapter only *formats* the result. Surface drift
is therefore structurally impossible — and the registry-driven parity test proves it.

## Nouns

| Noun | Is | Notes |
|---|---|---|
| `KB` | a knowledge base — the registry-addressable corpus | id, name, description, tags |
| `Source` | the raw evidentiary text (L3) with its own identity | rel path, install-state, `source_sha256` freshness — **distinct from Card**, because grounding must resolve against the authoritative source, not a derived card |
| `Card` | a derived representation of a Source at Levels of Zoom | meta → thin → full → (source) |
| `Lens` | distilled expertise — a discriminated union | `Dimension` (cross-thinker synthesis) \| `Framework` (one thinker) |
| `Citation` / `EvidenceHit` | a grounding result: phrase, source, line, context, **resolution status** | a first-class result, not a pass-through |
| `RawPassage` / `EvidenceSearchResult` | verbatim L3 passage(s), source line range, retrieval route, and an explicit sufficiency status | `evidence-found` means candidate text was retrieved, not that it entails an answer |

Inputs (not nouns): `Draft`, `Symptom`. Result-only: `Verdict`.

## Provenance (carried in every core result)

Provenance is STRUCTURED in the core result. The MCP `[kb]` text tag and the CLI `provenance` JSON
field are *adapter renderings* of it — never the contract.

    Provenance { kb, source_sha, source_version, op_id, op_version, policy }

## Grounding resolution taxonomy

`verify` proves textual **occurrence**, NOT claim truth or entailment. "Citation found" must never
read as "claim verified." Entailment (`entail`) is a separate, opt-in, advisory capability, never on
the default `verify` path. A grounding result carries exactly one of:

    found | ambiguous | folded-only | source-stale | source-not-installed | not-found

## Capability status

    stable | experimental | unavailable

An `experimental` op (e.g. `review`, whose judge is a stub) must self-label in its result
(`judge_identity`, `non_production=true`) or return `CAPABILITY_UNAVAILABLE`; it may never present an
authoritative-looking result. This status lives in the registry and gates every surface.

## Scope

Not every verb is KB-scoped. Scope is explicit per op:

    registry            # spans the registry (e.g. list kbs); no single KB
    kb(id)              # one KB
    kbs(ids | all)      # several KBs (fan-out)

## Operations

Canonical op-id ≠ public adapter name. The `mcp` column lists the public tool name(s) that project an
op (kept as compatibility aliases; `;`-separated when an op projects to more than one MCP tool). `—`
means the op is not projected on that surface. This table is machine-parsed — keep the columns.

| op-id | scope | result | capability | cli | mcp |
|---|---|---|---|---|---|
| kbs.list | registry | KBInfo[] | stable | kbs | list_kbs |
| lenses.list | kb | Lens[] | stable | lenses | list_lenses |
| cards.list | kb | Card[] | stable | cards | — |
| search | kb | Hit[] | stable | search | search_sources |
| diagnose | kb | DimensionRoute[] | stable | diagnose | diagnose |
| consult | kb | LensContent | stable | consult | consult_dimension;consult_framework |
| zoom | kb | CardContent | stable | zoom | zoom_card |
| verify | kb | EvidenceHit | stable | verify | verify_quote |
| evidence | kb | EvidenceSearchResult | stable | evidence | retrieve_evidence |
| review | kb | Verdict | experimental | review | — |
| init | registry | Scaffold | stable | init | — |
| build | kb | BuildReport | stable | build | — |
| check | kb | CheckReport | stable | check | — |
| normalize | kb | NormalizeReport | stable | normalize | — |
| ingest | kb | IngestReport | stable | ingest | — |

Notes on projections that legitimately differ per adapter (documented, not drift):
- `consult` projects onto TWO MCP tools (`consult_dimension`, `consult_framework`) — typed entry
  points for LLM clarity — but ONE CLI verb (`consult`, which auto-resolves the lens kind).
- `lenses.list` is `list_lenses` on MCP and `lenses` on the CLI; `kbs.list` is `list_kbs` / `kbs`.
- `cards.list` and `review` are CLI-only for now; `review` stays off MCP because its judge is
  experimental, so it is not yet an agent-facing tool.
- Authoring ops (`init`/`build`/`check`/`normalize`/`ingest`) are CLI-only maintenance verbs; they
  are registered for completeness but are not part of the agentic consumption surface.

## Compatibility aliases (MCP public name → canonical op-id)

Every existing MCP tool name keeps working; each maps to exactly one canonical op-id:

| mcp tool | op-id |
|---|---|
| list_kbs | kbs.list |
| list_lenses | lenses.list |
| search_sources | search |
| diagnose | diagnose |
| consult_dimension | consult |
| consult_framework | consult |
| zoom_card | zoom |
| verify_quote | verify |
| retrieve_evidence | evidence |
