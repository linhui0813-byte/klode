# Audit Findings — evidence-contract-slice

**Run**: audit-fix slice2 | **Scope**: main..HEAD | **Type**: full (9-dim) | **Fixer**: Claude
**Auditor**: Codex (lib `019fc845…`, gate `019fc851…`)
**Status**: open | fixed | deferred

| # | File | Sev | Finding | Fix | Status |
|---|------|-----|---------|-----|--------|
| 1 | services.py `_resolve_snapshot` | Critical | Line-location `marker.phrase in ln` ignores regex/#n/context → wrong occurrence/line; empty for a successful regex | Selector-aware `_locate` (regex/#n/before-after on raw lines) | fixed (R1/R2) |
| 2 | services.py `_resolve_snapshot` | Critical | Empty phrase / empty regex grounds arbitrary text (Marker("",regex=True)) | Reject empty phrase → NOT_FOUND | fixed (R1/R2) |
| 3 | services.py + parse | High | `#0` → nth=0 → resolve `len>=0` always true → fail-open | Reject nth<1 → NOT_FOUND | fixed (R1/R2) |
| 4 | review.py:94 | High | Judge bundle rebuilt from bare (card,phrase), losing the selector → a grounded #n/regex criterion sent an empty/rejected bundle | Pass the full Marker to build_context_bundle | fixed (R1/R2) |
| 5 | criteria.py:120 | High | Grounding.anchors reduces Marker to phrase (regex/#n/context discarded) — root cause of #4 | Store (Marker, card, line) in anchors | fixed (R1/R2) |
| 6 | criteria.py:113 | High | markers/phrases are independent fields; a divergent phrases=("missing",) with markers=(Marker("real"),) grounds via markers, omitting the fabricated phrase | Criterion.__post_init__ validates phrases mirror markers + markers are lib.Marker | fixed (R1/R2) |
| 7 | core.py EvidenceContext | Medium | `phrase` inserted mid-struct → positional-construction shift | Append the field | fixed (R1/R2) |
| 8 | criteria.py:92 | Medium | Guidance/[advisory] strip uses grep-only grammar; [advisory] inside a grep-re anchor false-flips criticality | Broaden the marker-strip to grep-re/search | fixed (R1/R2) |
| D1 | criteria.py:85 parse | Medium | parse_markers accepts valid prefixes of malformed markers (`#bogus`, `; )`) — the botched selector is dropped, but the phrase still must resolve (not a fabrication) | DEFERRED — strict parse is a linter-wide change in common.py; the dangerous #0 case is closed by #3 | deferred |
| D2 | core.RejectedContext | Medium | Loses selector/rel/source_sha; two pinned requests collapse | DEFERRED — richer identity; low value for single-card panels | deferred |
| D3 | services.py bundle | Medium | Unlocatable resolution rejected with FOLDED_ONLY as its "reason" (contradictory); per-request re-read (no batch) | DEFERRED — distinct SPAN_UNLOCATABLE state + batch read; book-sized local sources | deferred |
| D4 | services.py `_svc_review` | Medium | Public defect is an untyped 5-tuple; folded line=None | DEFERRED — a frozen ReviewDefect type is a separate refactor | deferred |
| D5 | mcp_server.py:360 | Medium | e.rel (card file: path) sits in the trusted region | DEFERRED — card paths are author-controlled (inside klode's trust model) | deferred |
| D6 | untrusted.py | Medium | wrap_untrusted is best-effort, not a hard guarantee | soften the docstring | fixed (R1/R2) |
| T1 | tests | Medium | Selector tests assert only .grounded, not the located line / bundle | Add #2-line, empty, #0, regex, divergence, bundle tests | fixed (R1/R2) |
