Xiaolai, I would not approve this plan as written. The architectural direction is defensible, but the execution plan contains one installation-breaking omission and an under-specified “shared console” contract that cannot currently preserve behavior.

## Findings, ranked by severity

### 1. Critical — P4 will break the installed package and the exact `python3 -m lodlib` invocation

Evidence:

- [pyproject.toml](/Users/joker/github/xiaolai/myprojects/lodlib/pyproject.toml:28) points console scripts at `lodlib.cli:main` and `lodlib.mcp_server:main`.
- [pyproject.toml](/Users/joker/github/xiaolai/myprojects/lodlib/pyproject.toml:32) explicitly packages only `["lodlib"]`. New `lodlib.core`, `lodlib.services`, and `lodlib.adapters` subpackages will not be included in a built wheel.
- [lodlib/__main__.py](/Users/joker/github/xiaolai/myprojects/lodlib/lodlib/__main__.py:4) imports `.cli`.
- P4 moves both modules to `adapters/`, but the move-map mentions neither `pyproject.toml` nor the `__main__.py` import.
- Existing tests call `cli.main(...)` in-process, e.g. [tests/test_lodlib.py](/Users/joker/github/xiaolai/myprojects/lodlib/tests/test_lodlib.py:874). That can pass from a source checkout while the built wheel is unusable.

Likely failures:

- `python3 -m lodlib -c ... check` raises `ModuleNotFoundError: lodlib.cli`.
- The installed `lib` and `lodlib-mcp` scripts point to removed modules.
- Even after correcting the entry points, `lodlib.adapters` and `lodlib.services` may be absent from the wheel.

Concrete fix:

- Add packaging and entry-point migration explicitly to P4.
- Change package discovery to include subpackages, such as setuptools package finding.
- Update `__main__.py` and both console-script targets.
- Prefer permanent compatibility shims at `lodlib/cli.py` and `lodlib/mcp_server.py` that re-export `main`. They are cheap and protect existing entry points and direct imports.
- Build a wheel, install it into a clean environment, and execute the exact external contract:

```text
python3 -m lodlib -c <fixture>/library.toml check
lib -c <fixture>/library.toml check
lodlib-mcp -c <fixture>/library.toml
```

This is the most likely phase to produce an outright break while all source-tree tests remain green.

### 2. High — P3’s `ConsultResult` contract is too weak to preserve current CLI and MCP semantics

P3 is the most likely phase to hide a behavioral regression.

The proposed API is only:

> `consult(cfg, name) -> ConsultResult`

with “outcome + payload + candidates + note” ([plan](/Users/joker/github/xiaolai/myprojects/lodlib/dev-docs/modularization-plan-2026-07-28.md:94)). That does not represent the inputs or policy currently supported.

Actual differences include:

- CLI dimension projection accepts `section` and `full`; writer/Craft is the default at [lodlib/cli.py](/Users/joker/github/xiaolai/myprojects/lodlib/lodlib/cli.py:259).
- MCP accepts `audience=writer|engine|full` plus a `section` override at [lodlib/mcp_server.py](/Users/joker/github/xiaolai/myprojects/lodlib/lodlib/mcp_server.py:92).
- MCP engine projection selects Craft plus headings containing `engine`, `operational spec`, or `scorer` at [lodlib/mcp_server.py](/Users/joker/github/xiaolai/myprojects/lodlib/lodlib/mcp_server.py:292).
- Full dimension output adds cards and gate information at [lodlib/mcp_server.py](/Users/joker/github/xiaolai/myprojects/lodlib/lodlib/mcp_server.py:300) and [lodlib/cli.py](/Users/joker/github/xiaolai/myprojects/lodlib/lodlib/cli.py:270).
- `consult_dimension` redirects a resolved framework/source to `consult_framework` instead of returning its content at [lodlib/mcp_server.py](/Users/joker/github/xiaolai/myprojects/lodlib/lodlib/mcp_server.py:265). Generic CLI consult dispatches directly to the resolved kind at [lodlib/cli.py](/Users/joker/github/xiaolai/myprojects/lodlib/lodlib/cli.py:327).
- Missing-section behavior is already different: CLI returns exit 1 at [lodlib/cli.py](/Users/joker/github/xiaolai/myprojects/lodlib/lodlib/cli.py:278), while MCP dimension consult falls back to the full body at [lodlib/mcp_server.py](/Users/joker/github/xiaolai/myprojects/lodlib/lodlib/mcp_server.py:286).
- Framework section selection differs in shape: CLI accepts one substring; MCP accepts an array and reports individual missing sections.

Therefore, “both format the same `ConsultResult`” is not well-defined. Either adapters retain projection/error/redirect policy, contradicting “format only,” or the console needs frontend-specific flags, which undermines the claimed shared mechanism.

Concrete fix:

Define the contract before moving code. At minimum:

- `ConsultRequest`: name, requested kind (`any`, `dimension`, `framework`), projection (`writer`, `engine`, `full`, `sections`), and requested section keys.
- `ConsultResult`: resolution outcome, typed dimension/framework/source payload, selected ordered sections, missing selections, candidates, tier/gate metadata, and projection diagnostics.
- Keep exit codes, MCP redirect wording, and protocol-specific fallback decisions in adapters unless you deliberately standardize them in a separate behavior-changing phase.

Do not claim adapters contain “zero policy.” A more honest invariant is: resolution, loading, and section-selection policy are shared; transport status and presentation remain adapter-owned.

### 3. High — the “exhaustive” move-map omits a load-bearing slice of `query.py`

The move-map assigns search, lenses, consult, and verify, but omits:

- `title`
- `card_path`
- `meta`
- `Source`
- `source_of`

See [lodlib/query.py](/Users/joker/github/xiaolai/myprojects/lodlib/lodlib/query.py:31), [lodlib/query.py](/Users/joker/github/xiaolai/myprojects/lodlib/lodlib/query.py:36), and [lodlib/query.py](/Users/joker/github/xiaolai/myprojects/lodlib/lodlib/query.py:112).

Those are not incidental:

- `search()` uses `sections()` and `title()` at [lodlib/query.py](/Users/joker/github/xiaolai/myprojects/lodlib/lodlib/query.py:75).
- `verify()` uses `source_of()` at [lodlib/query.py](/Users/joker/github/xiaolai/myprojects/lodlib/lodlib/query.py:486).
- CLI zoom uses the complete card slice at [lodlib/cli.py](/Users/joker/github/xiaolai/myprojects/lodlib/lodlib/cli.py:191).
- MCP zoom and source summaries do the same at [lodlib/mcp_server.py](/Users/joker/github/xiaolai/myprojects/lodlib/lodlib/mcp_server.py:250) and [lodlib/mcp_server.py](/Users/joker/github/xiaolai/myprojects/lodlib/lodlib/mcp_server.py:369).

The map also places generic `sections()` under `lenses`, although it parses ordinary source-card Thin/Full sections too. That forces `search` and card zoom to depend on a supposedly lens-specific module. If the omitted source functions are also dropped into `lenses`, `verify` acquires the same inappropriate dependency.

Concrete fix:

Introduce a coherent card-access module, for example `services/cards.py`:

- `sections`, `title`, `card_path`
- `meta`, `body`, `card_title`
- `Source`, `source_of`

Then:

```text
search  → cards
lenses  → cards/parser as needed
consult → lenses + cards
verify  → cards + core.matcher
console → consult + lenses/cards
```

`consult` genuinely depends on lenses: `resolve_consult()` calls `dimension()`, `framework()`, `_md_files()`, and diagnostics calls `dimension()`. That is a one-way dependency, not a cycle. Keeping `lenses` and `consult` together would also be reasonable at this size.

### 4. High — the plan claims to remove an import cycle that does not exist in the actual graph

The alleged cycle is the lazy import at [lodlib/query.py](/Users/joker/github/xiaolai/myprojects/lodlib/lodlib/query.py:324):

```python
from .build import framework_source_map
```

But `build.py` imports `common` and `config`; it does not import `query`. There is no `query → build → query` cycle in the current code.

Likewise, `check.py` lazily imports `bib_line_for` from build, but build does not import check.

Moving shared helpers can still improve ownership, but “breaks the one import cycle” is inaccurate evidence. Worse, the proposed `core/model.py` is not a coherent model:

- `source_sha256` is filesystem hashing.
- `framework_source_map` scans framework files.
- `bib_line_for` reads a bibliography.
- `humanize` is display-oriented string manipulation.

That is a utility drawer relabeled “domain model,” not a model abstraction.

Concrete fix:

- Correct the plan’s dependency graph.
- Move `framework_source_map` to lens/catalog indexing or a small repository/catalog module.
- Keep bibliography/build helpers with build unless another service genuinely needs them.
- Move `source_sha256` only if a shared provenance module is justified.
- Do not create `core/model.py` merely to satisfy the target diagram.

P1’s matcher relocation is otherwise mechanically reasonable, but “whole `common.py` is the pure matcher” is also inaccurate: `card_files`, `shelf_txts`, `read`, and `read_lenient` perform filesystem discovery and I/O at [lodlib/common.py](/Users/joker/github/xiaolai/myprojects/lodlib/lodlib/common.py:263). They are not pure domain logic.

### 5. High — “byte-identical” is asserted much more broadly than it is tested

P0 snapshots only consult and diagnose cases ([plan](/Users/joker/github/xiaolai/myprojects/lodlib/dev-docs/modularization-plan-2026-07-28.md:103)), but every command’s import path changes when CLI and service modules move.

The unprotected surfaces include:

- `init`
- `build`, including `--stamp` and the corpus-absent skip
- `check`, including `--quiet`, `--strict`, and optional entail degradation
- `ingest`
- `normalize`, including dry-run, `--apply`, and `--check`
- `search`
- all zoom levels and grep exit behavior
- MCP `tools/list`, initialization, error envelopes, and `isError`

Existing unit tests cover pieces of build/check/ingest/normalize, but rewriting their imports to new paths can make the refactor green while silently removing old module compatibility. `lib check 0/0` exercises one repository state, not the command’s output, exit-code matrix, or destructive filesystem behavior.

Concrete fix:

Add subprocess-level characterization tests before moving anything:

- Invoke `python -m lodlib`, not only `cli.main`.
- Assert stdout, stderr, and exit status separately.
- For mutating commands, compare a normalized filesystem manifest: paths, bytes, and hashes.
- Exercise build twice for idempotence and once with no installed corpus.
- Exercise normalize dry-run/apply/check.
- Exercise ingest failure and a deterministic mocked/supported tier.
- Send real JSON-RPC lines to `lodlib-mcp`; snapshot `initialize`, `tools/list`, representative tool calls, and errors.
- Add the installed-wheel smoke test described above.

Golden snapshots are useful, but the current proposed set cannot support the plan’s global “behavior byte-identical” claim.

### 6. Medium — the four-way `query.py` split is premature fragmentation in its proposed form

`query.py` is 499 lines, but the proposed boundaries do not match its cohesion:

- Search is independently cohesive.
- Verify is independently cohesive.
- Lenses, resolution, diagnostics, and gate lookup form one tightly coupled consult domain.
- Card anatomy/source lookup is a missing fourth concern.

Splitting lenses and consult buys little: consult must import most of lenses, and console then imports both. The directory structure turns one navigable module into several small pass-through modules without reducing mutation frequency or isolating a volatile dependency.

Concrete fix:

A smaller useful cut is:

```text
services/cards.py
services/search.py
services/consult.py   # lens loading + resolution + diagnose + gate
services/verify.py
services/console.py   # only after its contract is specified
```

Alternatively, keep a single `services/query.py` initially and split only when Loop B creates a demonstrated independent seam.

### 7. Medium — moving all flat operational modules adds churn without a demonstrated mechanism

The facade and shared consult application service have real mechanisms. Moving stable `build.py`, `check.py`, `ingest.py`, `normalize.py`, and `entail.py` under `services/` mostly changes import paths.

At 3.3k LOC, `core/services/adapters` is not automatically excessive, but the plan should distinguish structural value from taxonomy:

- Public facade: valuable.
- Shared consult contract: valuable if specified properly.
- Extracting genuinely shared matcher/card primitives: valuable.
- Relocating every operational module: largely ceremonial unless an enforced dependency violation exists today.

The existing graph is already mostly directional. `ingest → normalize` is a legitimate service-to-service dependency; the plan’s prose does not clearly say whether that is allowed.

Concrete fix:

Either keep operational modules flat behind the facade, or document the exact forbidden imports the move prevents. Add a pre-refactor dependency report showing current violations; do not move modules solely to fill directory boxes.

## Plan gaps summary

- **P0:** Missing command-wide and installed-package baselines.
- **P1:** Based on a nonexistent import cycle; misclassifies filesystem utilities as pure core/model logic.
- **P2:** Move-map is not exhaustive; card anatomy/source access has no destination.
- **P3:** `ConsultRequest` and typed payload/projection semantics are unspecified; cannot preserve MCP audiences and section behavior as written.
- **P4:** Omits `__main__.py`, `pyproject.toml`, package discovery, console scripts, and compatibility shims.
- **P5:** Useful in principle, but the import test must use `ast`, resolve relative imports, and detect dynamic imports. A text/regex scan will miss exactly the current lazy-import pattern.

## Additional missing items

- A compatibility policy for `lodlib.query`, `lodlib.common`, `lodlib.build`, `lodlib.cli`, and `lodlib.mcp_server`. The repository’s tests and evaluation tools import these directly.
- Explicit public API signatures, result dataclasses, exception semantics, and stability/versioning expectations.
- Verification that importing the new `lodlib` facade does not eagerly import adapters or optional entail/OCR dependencies.
- Tests for MCP tool schemas. `audience` enums, parameter names, descriptions, and tool names are external API.
- A test that Loop B imports only the facade. Calling `__init__.py` “the only surface” does not enforce that in Python; enforcement belongs in the consumer’s import/layer test.
- A repository-wide update list: `eval/bench.py`, `eval/retrieval.py`, and `eval/tokens.py` currently import `lodlib.common`.
- A rollback procedure that avoids destructive `git reset`; reverting a phase commit is safer once work is shared.

## What is sound

The curated facade is worth doing before Loop B. A shared resolution/loading layer is also justified by the demonstrated CLI/MCP drift. Per-phase commits and an AST-based layering guard are sensible.

But the plan needs a packaging phase and a real consult request/result specification before implementation. Without those, it is likely to produce a clean-looking source tree that either cannot be installed or subtly changes MCP behavior.

This was an inspection audit; I did not run the test suite.
