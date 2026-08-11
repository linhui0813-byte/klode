# Plan — extraction integrity, and a settings file for providers

**Status:** **v1 REFUTED — superseded by the revision below.** **Date:** 2026-08-11. **Target:** 0.4.0.

> **Audit outcome (Codex, refute mode, 2026-08-11).** Four of eight areas came back *fatal*, and I
> reproduced every load-bearing check before accepting it. The v1 design is kept below for the
> record; **§9 is what to build.** The three findings that change the design rather than refine it:
>
> 1. **The control is not ground truth.** `pdftotext` is the baseline precisely when escalation has
>    already judged it unreliable. Agreement establishes *"these differ"*, never *"the candidate is
>    damaged"* — a correct OCR reconstruction of a broken text layer scores *worse* than the broken
>    layer. This was my own stated worry and it is confirmed: the mechanism is a disagreement
>    detector, not an integrity verifier.
> 2. **Whole-book Spearman is at the wrong scale.** Reproduced exactly: 300 pages of 400 anchors
>    with *every page internally reversed* scores **ρ = 0.999978** — invisible. Moving the first 1%
>    of the document to the end scores **ρ = 0.9406** — a loud alert for 99%-intact local order. The
>    metric misses the failure it exists to catch and fires on one that barely matters.
> 3. **My prototype's 0.485 was an artefact of a global scramble.** Codex re-ran it page-locally on
>    real prose: half-swap ρ=0.9836, full page reversal ρ=0.9773 — nowhere near 0.485. The
>    containment (0.500) and inflation (2.00) figures survive; the order figure does not.
>
> Also fatal: `Coverage` measures the **control's** pages, so it is byte-identical whether the
> candidate kept every page or dropped half; "exit non-zero but still write" matches no existing
> semantic in this codebase and breaks retry (the file exists, so the retry needs `--force`); and
> anchor-resolution rate is biased toward whichever backend authored the anchors **and is
> order-insensitive**, so a fully scrambled extraction scores 100%.
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

---

# 9. Revision (v2) — what to actually build

The audit did not refine v1; it moved the foundation. v1 proposed a **gate** built on a control that
cannot arbitrate, scored by a metric at the wrong scale, with thresholds deferred to a measurement
scheduled *after* the gate. v2 inverts that: **build the measurement, gate nothing until it exists.**

That is the project's own rule (*measure before building*) applied to a plan that had violated it.

## 9.1 The reframe

| v1 claim | v2 position |
|---|---|
| Control-vs-candidate agreement **verifies** integrity | It is **telemetry**, not a verdict. It detects disagreement; it cannot say which side is right. |
| Whole-book Spearman detects scrambling | Replaced by **page/window-local** order scoring. Global ρ misses per-page reversal (0.999978) and overreacts to a moved block (0.9406). |
| `Coverage` answers "did we get every page?" | It answers that for the **control**. Candidate coverage needs candidate **page provenance**, which the layout model's structured output may already carry. |
| Anchor-resolution rate decides the backend | Demoted to a **compatibility** statistic. It is biased toward the authoring backend and order-insensitive — a fully scrambled text scores 100%. |
| Ship a gate, calibrate later | **No gating until a labeled corpus exists.** A gate whose acceptance test cannot be written is not implementable. |

## 9.2 Work items, re-sequenced

**WI-0 · A labeled, page-level extraction corpus.** *Now the first item, not a later one.* Real PDFs
— born-digital, scanned, two-column, table-heavy, footnoted, mixed-language, broken text layer, blank
pages — with page-level ground truth from rendered pages, not from another extractor.
*Check:* `find tests/fixtures -iname '*.pdf'` currently returns **0**. Nothing downstream can be
validated until this exists. Licensing matters: prefer public-domain or synthetic-but-real PDFs that
can be committed.

**WI-1 · Agreement primitives, scoped honestly.** `containment` and `inflation` survive the audit
unchanged — they are algebra and they behaved correctly on both synthetic and real prose. Order
scoring becomes **page/window-local displacement** (per-window rank agreement, or matched-shingle
order), reported per window with a distribution, never a single book-level scalar.
*Check:* reversing every page must fail while whole-book page order is intact; moving one block must
not dominate. Both permutation tests, on real prose, with a stated tokenizer (case, punctuation,
hyphenation, Unicode normalization all fixed and tested — they change anchor counts materially).

**WI-2 · Candidate page provenance.** Before rerunning a known-bad control, ask whether the layout
backend's structured result already carries page numbers / bounding boxes. If it does, candidate
coverage becomes direct rather than inferred.
*Check:* for 20 PDFs, measure declared-pages-represented, blocks lacking page provenance, duplicate
page assignments, and runtime cost. If the structured output cannot supply it, fall back to sampled
rendered-page OCR and compare cost against a second full extraction.

**WI-3 · Sampled visual ground truth.** The only thing that actually establishes fidelity: render N
random pages, OCR them, compare against the candidate's text for those pages. Record the seed and
the page numbers so the sample is reproducible and auditable.
*Check:* report per-failure recall and clean-document false-positive rate with intervals — not four
point values.

**WI-4 · Verification states and transactional semantics.** Replacing v1's incoherent
"non-zero but still write":
- **default:** verification failure **refuses promotion to the shelf** and exits non-zero — matching
  `ingest.py`'s existing pre-write guards, which is the semantic already in this codebase;
- `--accept-unverified`: writes, exits **zero**, and persists `verification_status="unverified"`;
- states are `verified | unverified | abstained | failed`.
*Check:* an end-to-end test asserting **either** success + promoted artifact **or** failure + no
shelf mutation. Never both. (v1 failed this: the retry would hit an existing file and demand
`--force`.)

**WI-5 · Provenance bound to the persisted bytes.** Verification must describe **what was written**,
not what was extracted — v1 verified pre-normalization, but `normalize.process()` produces the shelf
artifact and can strip a legitimate repeated refrain as a running head. Record the final output
sha256, both backends' versions, metric-schema version, thresholds, decision, candidate coverage,
and status.
*Check:* changing one byte of the shelf source must invalidate the matching verification record; a
forced re-ingest must not inherit the previous verification. A card front-matter field is the wrong
home — `build.py` rewrites machine-managed front matter and would drop it.

**WI-6 · Settings (Track B), split out entirely.** Independent of the extraction hole; ship it
separately. Two concrete defects the audit found in v1's version:
- `--tier` defaults to `"auto"`, so an omitted flag and an explicit `--tier auto` are
  indistinguishable and the precedence chain is **unimplementable as written**. Parser defaults must
  be `None`/`SUPPRESS`, with `BooleanOptionalAction` for paired boolean flags.
  *Check:* parse both forms; assert the namespaces differ. They currently do not.
- The scope was overclaimed: this configures a **model and default tier**, not "providers" — there
  is exactly one transport (Anthropic) and `KLODE_DOCLING_URL` stays in the environment. Either add a
  validated `provider` contract or narrow the wording.
- Add `klode settings show --effective --sources`, or the split surface is not auditable.
  *Check:* a table-driven precedence matrix that names both the winning value and its origin.

**WI-7 · marker bake-off.** Unchanged in intent, corrected in metric: ranked by WI-1/WI-2/WI-3
against WI-0's ground truth. Anchor-resolution rate is reported as *compatibility*, never as the
decider.
*Check:* backend ranking must not reverse when anchors are chosen from a different backend's output.
If it does, the metric measured authoring compatibility, not fidelity.

## 9.3 Known blind spot, accepted and recorded

No combination of containment / inflation / local order / coverage detects a **value transposition**
that preserves the bag, the length, and the order — e.g. a table extractor swapping two status cells:

```
control:   Alice status approved   Bob status rejected
candidate: Alice status rejected   Bob status approved
```

Containment 1, inflation 1, order 1, coverage full — both claims inverted. Only semantic or
positional (bounding-box) comparison catches it. **Recorded as out of scope**, not solved: any
table-derived claim needs human verification, and that limit belongs in the docs beside the existing
"referential, not semantic" boundary rather than being silently hoped away.

## 9.4 The assumption to keep watching

That agreement with `pdftotext` is evidence of fidelity to the **rendered** PDF — on the very path
where `pdftotext` was already classified unreliable. Until WI-3 puts rendered-page ground truth
underneath it, everything above is a disagreement detector. Useful, worth having, and not what the
opening section of this document asked for.
