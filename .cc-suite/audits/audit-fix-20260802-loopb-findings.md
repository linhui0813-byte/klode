# Audit Findings

**Run**: audit-fix loopb-gate-hardening | **Scope**: main..HEAD (5 changed source files) | **Type**: full (9-dim)
**Auditor**: Codex (2 read-only calls: lib layer `019fc203…`, gate layer `019fc20d…`) | **Fixer**: Claude
**Status values**: open | fixed | not-fixed | partial | deferred

| # | File:Line | Sev | Dim | Finding | Fix | Status |
|---|-----------|-----|-----|---------|-----|--------|
| 1 | services.py verify_context | High | D3 | Window cap anchored on `lo`, can EXCLUDE the match (ctx=500,max=1 → line 1 while match at 4) | Cap centered on match span; anchor always inside | fixed | R2 |
| 2 | services.py verify_context | High | D2 | Negative `max_window` → negative slicing discloses ~whole source; no input validation | Validate context_lines>=0, max_window>=1 (ValueError) | fixed | R1 |
| 3 | services.py verify_context | High | D3 | Folded-unlocatable returns `usable=True` with no span — violates contract | Return usable=False | fixed | R1 |
| 4 | services.py _locate_folded | Med | D3 | IGNORECASE can locate wrong-case occurrence (literal matching is case-sensitive); materializes huge range tuple; redundant `if w` | Case-sensitive; store span endpoints; drop `if w` | fixed | R1 |
| 5 | services.py verify_evidence | Med | D3 | `today or date.today()` wrong sentinel; `today` untyped | `date.today() if today is None else today`; annotate | fixed | R1 |
| 6 | services.py:169 / core.py:25,154 / __init__.py | Med/Low | D9 | Docstrings overstate: unstamped "cannot ground" (default permits it); enum SOURCE_STALE mislabeled evidence-only; "never dump whole source" false for short/long-line sources; superseded_by not enforced | Narrow all claims to what's enforced | fixed | R2 |
| 7 | criteria.py:74-80 | High | D3 | Parser FAIL-OPEN: a bold move with missing/malformed `grep:` is silently dropped → reduced rubric can score Go | Raise on an unanchored bold move (no silent skip) | fixed | R1 |
| 8 | criteria.py:38 _GREP_MARKER | Med | D3 | Marker regex closes at first `)`, corrupting guidance for anchors containing `)` | Backtick-aware marker pattern | fixed | R1 |
| 9 | criteria.py:79 [advisory] | Med | D3 | `[advisory]` matched anywhere incl. inside anchors → false positives | Detect on marker-stripped text only | fixed | R1 |
| 10 | criteria.py:95-103 | Med | D3 | Ungrounded `reason` overwritten by last card → a freshness failure hidden behind a later not-found | Precedence: freshness failure over not-found | fixed | R1 |
| 11 | criteria.py:24 / review.py | Med | D3 | `criticality` documented "feedback-only" but never enforced (all criteria gate) — contradiction | Clarify: recorded, not yet enforced (all gate) | fixed | R1 |
| 12 | review.py:24-30 | Med | D4 | `Verdict.ungrounded` removed with no compat alias | Add deprecated `ungrounded` property | fixed | R1 |
| 13 | review.py:5-7 / criteria.py:51 | Low | D9 | Module docstring still says "dropped and flagged" + Go/Recycle; `_panel` says verify() | Update docstrings | fixed | R1 |
| 14 | tests | Med | D7 | Weak/absent tests: bounded-window needle presence, folded-unlocatable, parser-omission, parens-in-anchor, advisory-in-anchor, input validation, reason precedence | Add targeted tests | fixed | R1 |
| D1 | services verify_context | Med | D6 | Whole-source read / up to 3 reads / no cache | DEFERRED — matches existing klode read pattern for book-sized sources | deferred |
| D2 | services verify_context | Med | D3 | TOCTOU: verify and context use different fs snapshots | DEFERRED — local single-user; would require threading one snapshot through the shared verifier | deferred |
| D3 | criteria.py:96 | Med | D3 | First-card-wins provenance in multi-card panels | DEFERRED — all current panels are single-card; documented as policy | deferred |
| D4 | gate default | Med | D2 | require_stamp=False default (unstamped grounds) | DEFERRED (default) — narrowing docstrings instead (finding 6); production sets require_stamp=True | deferred |
