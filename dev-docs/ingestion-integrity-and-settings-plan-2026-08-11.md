# Plan — extraction integrity, and a settings file for providers

**Status:** plan, not yet built. **Date:** 2026-08-11. **Target:** 0.4.0.
**Scope:** three questions, answered in dependency order.

1. Can a user configure LLM providers and docling from a file? *(No — env vars and a constructor
   argument. The judge shipped in 0.3.0 is unreachable from the CLI.)*
2. Should we support `marker` alongside docling? *(Unknown, and it should stay unknown until
   measured. This plan builds the measurement, not the backend.)*
3. After a docling extraction, how do we know the text is intact? *(**We don't.** Demonstrated
   below. This is the load-bearing item.)*

---

## 1. The hole, demonstrated

`klode ingest` already guards the obvious failures: `>10%` control chars is refused outright,
`words == 0` is refused before and after normalization, and `corruption_score` (tilde-in-word +
mid-word caps per 10k words) drives OCR escalation. That guard is real and it works — **for OCR
garble**, which is what it was tuned against.

Docling does not fail that way. Running the current gate over synthetic versions of its
characteristic failures:

| variant | corruption/10k | words | verdict today |
|---|---:|---:|---|
| clean | 0.00 | 684 | ACCEPTED |
| **column-scrambled (reading order)** | **0.00** | 684 | **ACCEPTED** |
| **half the pages dropped** | **0.00** | 342 | **ACCEPTED** |
| **table cells / headers duplicated** | **0.00** | 1368 | **ACCEPTED** |
| OCR garble | 526.32 | 684 | rejected → escalated |

Every layout-model failure scores a **perfect 0.00**. Every word is a real word, correctly cased;
`corruption_score` is structurally blind to order, coverage, and duplication.

**Why this matters more here than in a normal pipeline.** An anchor authored against scrambled text
*resolves forever*, because the scrambled text is stable on disk. `klode check` stays green over a
corpus that misrepresents the book. The linter's guarantee is referential and never promised to
catch this — but every downstream claim, rubric, and verdict rests on the corpus being the source.

## 2. Constraints any solution must respect

Taken from the existing design record, not invented here:

- **Zero runtime dependencies.** Everything below is stdlib + poppler (already required for
  `pdftotext`; `pdfinfo` ships in the same package).
- **Measure before building.** The research conclusion refused to adopt BM25 on intuition and built
  a 15-question eval set first. The same rule governs `marker`: build the comparison, then decide.
- **Prefer the loud default.** A check that degrades quietly is a defect generator.
- **Env, not config, for private endpoints.** `pdf.py:28` keeps `KLODE_DOCLING_URL` in the
  environment on purpose — it names an internal host and `library.toml` is tracked. Any settings
  file must not undo that.
- **Backends stay lazy.** Importing `klode.lib.formats` must continue to pull in no backend.

---

# Track A — extraction integrity

The priority. WI-A1 also unblocks Track C.

## WI-A1 · `klode/lib/agreement.py` — comparison primitives

**Why.** The strongest cheap signal for a layout-model failure is a *second opinion*: run the cheap
deterministic extractor as a control and compare. Content disagreement and order disagreement are
different failures and must be reported separately — that separation is the whole point.

**Contract** (pure, no I/O, no backend):

```python
@dataclass(frozen=True)
class Agreement:
    containment: float   # |bag(a) ∩ bag(b)| / |bag(a)|  — how much of the control survived
    inflation: float     # |bag(b)| / |bag(a)|           — >1 means b duplicated material
    order: float | None  # rank correlation over unique shared tokens; None when too few
    unique_anchors: int  # how many tokens the order figure rests on

def compare(control: str, candidate: str) -> Agreement
```

**Order correlation must not use LCS.** A naive longest-common-subsequence over two ~200k-token
books is O(n·m) and will not finish. Instead: take tokens occurring **exactly once in both** texts
(natural anchors), read off their positions in each, and compute a rank correlation (Spearman;
stdlib arithmetic). O(n log n), robust to insertions, and it measures precisely the thing
`corruption_score` cannot — whether the *sequence* survived.

**Verification.** The five-variant table above becomes the test matrix, and each variant must land
in a distinct quadrant:

| variant | containment | inflation | order |
|---|---|---|---|
| clean | ~1.0 | ~1.0 | ~1.0 |
| column-scrambled | ~1.0 | ~1.0 | **low** |
| pages dropped | **low** | <1 | ~1.0 on what remains |
| duplicated | ~1.0 | **~2.0** | ~1.0 |

A test that only asserts "clean scores well" would be vacuous; each row must be distinguishable from
every other.

**Risk.** Rank correlation needs enough unique shared tokens. Below a floor (say 200), return
`order=None` rather than a number computed from noise — an honest abstention, consistent with the
gate's `Unavailable`.

### Prototype result (run 2026-08-11, before committing to the design)

The approach was validated on synthetic 4000-token documents rather than assumed:

| variant | containment | inflation | order | unique anchors |
|---|---:|---:|---:|---:|
| clean | 1.000 | 1.00 | 1.000 | 4000 |
| column-scrambled | 1.000 | 1.00 | **0.485** | 4000 |
| half dropped | **0.500** | 0.50 | 1.000 | 2000 |
| duplicated | 1.000 | **2.00** | *n/a* | **0** |

Each failure lands in a distinct quadrant, and every one is invisible to `corruption_score`. Two
things this measurement changed in the design:

- **Duplication collapses the anchor set to zero.** Repeating the text makes every token occur
  twice, so no token is "unique in both" and `order` correctly abstains — precisely when
  `inflation` is the signal that matters. This is correct behaviour, not a gap, but it must be
  documented or `order: n/a` will read as a broken check. The two signals are complementary by
  construction, and neither alone is sufficient.
- **Scrambling scores 0.485, not ~0.** Block-level reordering preserves substantial local order, so
  the threshold cannot be "near 1.0 or fail". The separation (0.485 vs 1.000) is clean, but where to
  cut is an empirical question — deferred to WI-C1, like `CLEAN_THRESHOLD = 5.0` was before it.

## WI-A2 · Coverage — did we get every page?

**Why.** A docling run that silently drops pages 40–60 is invisible today: the remaining text is
clean, plentiful, and scores 0.00.

**Mechanism.** `pdfinfo` reports `Pages:`; `pdftotext` emits `\f` between pages. Split the control
extraction on `\f`, count words per page, and report pages yielding under a floor.

```python
@dataclass(frozen=True)
class Coverage:
    pages_declared: int
    pages_with_text: int
    empty_pages: tuple[int, ...]     # 1-indexed
```

**Verification.** A fixture PDF with known-blank pages flags exactly those and no others. Where
`pdfinfo` is absent, return `pages_declared=0` and say so — never silently report full coverage.

**Note.** Coverage is measured on the *control* (pdftotext), which knows page boundaries. Docling
markdown has no reliable page separator; comparing its word total against the control's per-page
totals is what surfaces a dropped range.

## WI-A3 · Wire it into `ingest`

**Where.** `ingest.py`, after `handler.extract` and before the write — the existing guard block
already sits there and this joins it.

**When it runs.** Only when the chosen handler is *not* the control (`tier != "pdftotext"` on PDFs).
For a clean text layer there is nothing to second-guess, and ingestion cost stays unchanged for the
common case.

**Block or warn?** — the one genuine design decision here.

- Refusing outright is the project's instinct, but a false refusal blocks a legitimately hard PDF,
  and thresholds are uncalibrated on day one.
- Passing quietly is the failure this whole plan exists to remove.

**Recommendation:** report loudly and **exit non-zero** by default while still writing the source,
and refuse the write under `--strict-verify`. The numbers always land in provenance. Once thresholds
are calibrated against the real corpus, flip the default to refuse. Stated here so the change is a
decision with evidence rather than a drift.

**Verification.** An ingest whose candidate disagrees with the control produces a non-zero exit and
a report naming which signal failed; `--strict-verify` leaves no file on disk.

## WI-A4 · Persist and surface

**Why.** *"When a silent failure is fixed, leave an assertion, not just a fix."* Without this, the
verification runs once and is forgotten.

- Extend `PROVENANCE.jsonl` with `containment` / `inflation` / `order` / `pages_declared` /
  `empty_pages` (additive; it is already JSONL).
- Add an optional card front-matter field recording that the extraction was verified, and by which
  control.
- `klode check` **WARNs** when a card's source was ingested by a non-control tier and carries no
  verification record — the same shape as the existing `review_by` freshness warning.

**Verification.** A card ingested via docling with no verification record produces exactly one WARN;
adding the record clears it.

---

# Track B — configuration

Independent of Track A; can proceed in parallel.

## WI-B1 · `~/.klode/settings.toml`

**Why.** There is no settings file today. `library.toml` is per-KB and tracked; provider choice is
neither. `~/.klode/registry.toml` already establishes the user-level, untracked location.

```toml
[judge]
model       = "…"      # REQUIRED to use a real judge; no default (see below)
permutations = 2
hurdle       = 60

[ingest]
default_tier  = "auto"
strict_verify = false
```

**Precedence:** explicit argument → environment → settings file → built-in default. A missing file
is not an error (same as the registry).

**Secrets stay out.** API keys remain environment-only (`ANTHROPIC_API_KEY`), and
`KLODE_DOCLING_URL` remains environment-only for the reason already recorded in `pdf.py`. The
settings file holds *choices*, never credentials or private hosts.

**No default model, deliberately.** Self-enhancement bias means the judge must differ from whatever
produced the draft. `LLMJudge` already requires `model` for that reason; the settings file makes the
choice recordable, not automatic.

## WI-B2 · Make the judge reachable

**Why.** `LLMJudge` shipped in 0.3.0 but `klode review` builds a `FixtureJudge` internally and never
passes one through. A user who installs klode cannot use it.

- `klode review --judge {fixture,llm} [--model X]`, resolved through WI-B1 precedence.
- A missing API key fails loud before any work.
- The result keeps reporting `non_production` while uncalibrated — WI-B2 changes *reachability*, not
  authority.

**MCP stays closed.** `review` remains unprojected (`opspec.py` `mcp=()`). Exposing an uncalibrated
judge to an agent invites it to treat a verdict as authoritative, which is exactly what the
calibration gate exists to prevent. Revisit after a rubric is calibrated, not before.

---

# Track C — the `marker` question

## WI-C1 · `eval/extract_bakeoff.py`

**Why.** "Should we support marker?" is an empirical question about *your* PDFs, and the honest
answer is a measurement. This harness is also the natural home for WI-A1's primitives.

Per backend (`pdftotext`, `xberg`, `docling`, and `marker` if installed), over a sample of real
sources:

| metric | what it catches |
|---|---|
| `corruption_score` | OCR garble (existing) |
| coverage (WI-A2) | dropped pages |
| containment / inflation / order (WI-A1) | scrambling, duplication, loss |
| **anchor-resolution rate** | the one that matters: take existing `(grep: …)` anchors from cards and count how many still resolve in each backend's output |

Anchor-resolution rate is the decisive metric because it measures the property klode actually
depends on, using evidence already authored.

**Verification.** The harness runs over the committed fixtures without any optional backend
installed (reporting the absent ones as skipped), and over a real sample when they are.

## WI-C2 · marker as Tier 4 — **conditional**

Only if WI-C1 shows marker beating docling on the corpus. Same treatment as docling: lazy import,
optional extra, remote-endpoint escape hatch, no change to the zero-dependency default path. If the
measurement does not favour it, this item is closed with the numbers recorded — a decision, not a
backlog item.

---

## Sequencing

```
WI-A1 (primitives) ──┬── WI-A2 ── WI-A3 ── WI-A4      Track A: the integrity hole
                     └── WI-C1 ── WI-C2 (conditional)  Track C: decides marker
WI-B1 ── WI-B2                                          Track B: parallel, independent
```

WI-A1 first: it is pure, fully testable without any backend, and both other tracks consume it.

## What this plan does not do

- It does not make `corruption_score` smarter. That metric is correct for what it measures; the fix
  is a second signal, not a better single one.
- It does not calibrate thresholds. Day-one values are guesses; WI-C1 is what turns them into
  measured numbers, and WI-A3's block/warn default should flip only after that.
- It does not touch the judge's calibration gap. Unrelated, and still the blocking item for trusting
  any verdict.

## Owned uncertainty

- **Rank correlation on real books is unvalidated.** It is the right shape, but whether a
  column-scrambled real PDF separates cleanly from a legitimately reordered one (footnotes, sidebars
  hoisted by a layout model) is unknown until WI-C1 runs. If it does not separate, the order signal
  becomes advisory and coverage + containment carry the weight.
- **Threshold calibration needs a sample you consider representative.** I can pick one; you would
  pick a better one.
- **`marker`'s current quality is not something I can assert.** That is precisely why WI-C1 exists
  rather than a recommendation.
