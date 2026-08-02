# Audit Findings

**Run**: audit-fix 20260802-103907 | **Scope**: whole project (klode/lib/*, klode/gate/*) | **Audit type**: mini (5-dim)
**Model**: Claude (3 parallel reviewers; Codex runner stalled this session) | **Fixer**: Claude
**Status values**: open | fixed | not-fixed | partial | regressed | skipped

| # | File | Line | Severity | Dimension | Finding | Fix | Status | Round |
|---|------|------|----------|-----------|---------|-----|--------|-------|
| 1 | klode/lib/check.py | 325 | HIGH | security/error-handling | Copyright-leak guard failed OPEN on any git error (e.g. dubious-ownership exit 128) — treated as "not a repo" and passed | Distinguish "not a git repository" (stderr) → N/A from other git errors → ERROR (fail closed) | fixed | 1 |
| 2 | klode/lib/check.py | 335 | MEDIUM | security/edge-case | Leak guard failed OPEN for non-ASCII corpus filenames (`git ls-files` C-quotes → trailing `"` defeats `.endswith(".txt")`) | `ls-files -z` + split on NUL (no quoting) | fixed | 1 |
| 3 | klode/lib/check.py | 110 | MEDIUM | security | `check --entail` read the card `file:` with no containment (unlike rot/freshness checks) → arbitrary-file read / hang | Apply `SRC_RE.fullmatch` + realpath-within-lib gate | fixed | 1 |
| 4 | klode/lib/common.py | 236 | MEDIUM | correctness | `resolve` literal-with-context branch ignored `m.nth` → a `#n` anchor passed on ≥1 context match even if <n exist (fail-open in the linter) | Honour `nth` in this branch, like the other two | fixed | 1 |
| 5 | klode/lib/build.py | 123 | MEDIUM | correctness | Same filename stem on two shelves collides onto one flat card id — second overwrites first, silently mis-attributes citations | `_enumerate_sources` raises `ConfigError` on duplicate stems | fixed | 1 |
| 6 | klode/lib/normalize.py | 305 | MEDIUM | destructive-write | `prune_backups` with `backup_keep<=0` deletes the freshly-written backup → overwrite becomes irreversible | Clamp `runs[max(1, keep):]` | fixed | 1 |
| 7 | klode/lib/cli.py | 511 | MEDIUM | error-handling | `klode review` crashes with an uncaught traceback when the gate raises `ValueError` (corpus absent → nothing grounds); MCP catches all | `cmd_review` catches `ValueError` → clean message + exit 1 | fixed | 1 |
| 8 | klode/lib/mcp_server.py | 236,241 | LOW | error-handling | `_params_for` eagerly `int()`s untrusted `limit`/`max_lines`, bypassing `_bounded_int` → generic failure + traceback | Pass raw values through; `_bounded_int` coerces safely | fixed | 1 |
| 9 | klode/lib/common.py | 136 | LOW | consistency | `CTRL_RE` omitted `\x7f` (DEL) that `config._CTRL_RE` folds → a DEL byte could false-fail a citation | Add `\x7f` to the class | fixed | 1 |
| 10 | klode/lib/query.py | 165 | LOW | edge-case | A card `file:` resolving to a directory → `source_of` reports installed → `verify` raises `IsADirectoryError` | `source_of` requires `is_file()` | fixed | 1 |
| 11 | klode/lib/config.py | 121,130 | LOW | error-handling | Non-string TOML paths `str()`-coerced silently; `cards`/`bib`/etc. lack the containment guard `lib` has | — | skipped | — |
| 12 | klode/lib/cli.py | 461 | LOW | consistency | `consult --section <miss>` returns exit 1 in prose but 0 under `--json` | — | skipped | — |

**Skipped rationale:** #11 acts on *trusted* hand-edited config — validation churn is disproportionate to the risk. #12 is a niche exit-code edge whose fix needs a `missing`-flag plumbed through `DimensionResult`/console, disproportionate to a LOW.

**Clean modules (no defects):** core, opspec, services, mcp_server (aside from #8), console, registry, pool, entail, gate/{__init__,criteria,judge,review}. formats/* and ingest.py were hardened in earlier rounds this session.
