# Audit Findings

**Run**: audit-fix 20260813–14 | **Scope**: `main..HEAD` on `fix/audit-2026-08-13`
**Audit type**: full, per-file | **Auditor**: independent (Codex), verified before every fix

## How the audit was actually run

Three whole-branch attempts were launched and killed externally before producing anything. The
run that worked was **per-file and resumable** — one job per changed file, each writing its own
log — so a kill costs one file instead of everything. Two stall causes were diagnosed and fixed
along the way: `codex exec` waiting on a stdin that never closed in a backgrounded context (fixed
with `< /dev/null`), and whole-branch scope taking longer than the runs survived.

**Every finding below was re-verified by Claude before being fixed.** Two of the audit's claims
did not survive that check and are recorded as such.

---

## Round 1 — the original investigation (13 defects)

Eleven planned findings plus two found during implementation. All fixed, all repros re-run and
dead (20/20), each fix reverted individually to confirm its tests fail without it. Details in
`dev-docs/fix-plan-2026-08-13.md`.

One was a **regression introduced by my own fix** and found by reading the diff, not by a test:
`_load_doc(allow_stale_approval=True)` demoted the caller's document, silencing `cmd_repin`'s
"admission reset" notice — so repin revoked a human's approval without saying so. 945 tests were
green over it.

---

## Round 2 — the independent per-file audit (11 findings)

**Four of these were inside guards written hours earlier in round 1.**

| # | File:line | Severity | Finding | Status |
|---|---|---|---|---|
| 1 | `settings.py:331` | **High** | `0170000000` is 10.33.254.128 to Python's `int()` and 1.224.0.0 to the resolver, which reads a leading zero as octal. The guard classified one address; the socket dialled another. Round 1 handled the *dotted* octal form only. | fixed |
| 2 | `settings.py:363` | **High** | `urlsplit().hostname` keeps percent-escapes, so `8%2e8%2e8%2e8` reached the classifier with no dot, took the container-name branch, and was allowed — urllib decodes to 8.8.8.8 before connecting. | fixed |
| 3 | `check.py:87` | **High** | The round-1 tripwire only rejected a card count of ZERO. One card of two enumerated → `ok=True`, `errors=[]`, one card never read. Partial blindness is worse: the number on screen looks plausible. | fixed (set comparison) |
| 4 | `check.py:263` | **High** | The frameworks and syntheses loops had no guard at all; `glob` turns a read error into `[]` and neither records anything. The real KB has 64 frameworks and 23 syntheses behind that gap. | fixed (all three swept) |
| 5 | `check.py:428` | **High** | Inherited `GIT_INDEX_FILE` makes `rev-parse` still say "yes, a work tree" while `ls-files` reads an empty index. Verified against *this* repo: `ok=True` with two corpus files tracked. | fixed (git env scrubbed) |
| 6 | `check.py:436` | Medium | The in-tree backup sweep looked for `.normalize-backup-*`; `normalize.py` writes `normalize-backup-<stamp>`. **It had never matched anything.** | fixed |
| 7 | `check.py:398` | Medium | A missing git *binary* was reported as a clean N/A, which says nothing about whether the repository exists. | fixed (now `unmeasured`) |
| 8 | `common.py:289` | Medium | `glob.escape` was two-thirds of a fix: an escaped `[category]` is still a live bracket expression, so glob must LIST the parent — an unlistable parent gives `[]` with no error. Also inert for absolute patterns. | fixed (`root_dir=`) |
| 9 | `config.py:169` | Medium | The leading-dash rule checked raw shelf names, so `[library].dir = "--format="` composed the pathspec `--format=/books` and got through. | fixed (validates the composed path) |
| 10 | `config.py:123` | Medium | **My regression.** Moving `.resolve()` into `_as_path` stopped it covering the *discovered* path, so a symlinked `library.toml` anchored relative settings at the link's directory when found and the target's when named. | fixed |
| 11 | `config.py:65,111,146` | Low | `Path.cwd()` ran outside the coercion helper (deleted cwd leaked `FileNotFoundError` from both public APIs); TOML-derived paths still resolved raw (embedded NUL leaked `ValueError`). | fixed |

### Round 2, continued — `review.py`

| # | File:line | Severity | Finding | Status |
|---|---|---|---|---|
| 12 | `services.py:404` | **High in effect** | `review_draft` refuses a non-integer hurdle; the review SERVICE coerced with `int(...)` first, so the guard never saw one. Truncation only ever moves the bar DOWN — asking for 59.6 silently applied 59 and passed a 59.5238 draft. `int(False)` is 0. MCP and CLI both use this path. | fixed |
| 13 | `review.py:37` | Low | `Line.pct` kept the round-vs-exact contradiction: a 2/3 criterion at hurdle 67 displayed as 67 while being classified a defect below it. | fixed (floor) |

### Found by Claude while reviewing the above

| # | File:line | Severity | Finding | Status |
|---|---|---|---|---|
| 14 | `build.py:116` | **High** | `check` only reports; `build` REWRITES INDEX.md. The check-side tripwire does nothing for the writer, and the existing safeguard cannot see BOTH enumerations coming back empty. Verified: with enumeration stubbed empty, build overwrote a two-card INDEX and reported success. | fixed |

### Claims that did NOT survive verification

- *"the bracketed shelf `books[1]` breaks the git pathspec"* — it does not; git prefix-matches a
  directory pathspec. The real, measured effect of `--literal-pathspecs` is the opposite
  direction: a shelf named `books*` also matched a sibling `booksOTHER/`, reporting files outside
  every guarded shelf as leaks. Comment and test corrected to the demonstrated case.
- *"`workflow_call` is unverified"* — closed by evidence rather than argument: dispatching the
  release workflow on a non-tag ref ran all six `Test suite /` jobs through the reusable-workflow
  call, ran `build`, and skipped `publish`.

### One class fixed three times before it was actually closed

The round-1 rounding defect lived in **three** places: the verdict, the service boundary, and the
per-criterion display. Fixing the first and calling the class closed was wrong. A class is closed
when you have grepped for it, not when you have fixed the instance that bit you.

---

## Verification at the time of writing

- **970 tests**, `OK (skipped=1)`; every fix reverted individually to confirm its tests fail
- CI green on the pushed branch, all six jobs across Python 3.11–3.14
- Release gate proven end to end: `workflow_call` runs, `build` gated, `publish` skipped off-tag
- Real corpus: `storycraft` 179 cards / 179 sources / 6 shelves, 66/66 anchors grounded across 40
  cards; `aposd` clean. Both `klode check` exit 0
- Clean-venv install: zero third-party imports on the runtime path, both entry points resolve
- Diff scanned for secrets, credentials, private IPs and internal hostnames — clean

## Still open

`build.py` and `llm_judge.py` have not been through the independent per-file audit. Everything
else on the branch has.
