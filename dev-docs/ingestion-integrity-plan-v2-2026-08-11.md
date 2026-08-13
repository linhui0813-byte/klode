# Plan v2 — extraction integrity

**Status:** current. **Date:** 2026-08-11. **Target:** 0.4.0.
**Supersedes:** [`ingestion-integrity-and-settings-plan-2026-08-11.md`](ingestion-integrity-and-settings-plan-2026-08-11.md)
(v1, refuted — kept as the record of what was proposed and why it was wrong).

## The problem, in one paragraph

`klode ingest` guards binary-as-text and OCR garble well, but `corruption_score` is tuned for OCR
failure modes (tilde-in-word, mid-word caps). Every characteristic **layout-model** failure —
column-scrambled reading order, silently dropped pages, duplicated table cells — scores a perfect
**0.00** and is accepted. Every word is a real word, correctly cased. That matters more here than in
a normal pipeline: an anchor authored against scrambled text *resolves forever*, because the
scrambled text is stable on disk, so `klode check` stays green over a corpus that misrepresents the
book. v1 demonstrated this and it stands.

## Why v1 was refuted

An external refute-mode audit returned *fatal* on four of eight areas; every load-bearing check was
reproduced locally before being accepted. The three that moved the design:

1. **The control is not ground truth.** `pdftotext` is the baseline precisely when escalation has
   already judged it unreliable. Agreement establishes *"these differ"*, never *"the candidate is
   damaged"* — a correct OCR reconstruction of a broken text layer scores *worse* than the broken
   layer.
2. **Whole-book Spearman is at the wrong scale.** Reproduced exactly: 300 pages of 400 anchors with
   *every page internally reversed* → **ρ = 0.999978** (invisible). Moving the first 1% of the
   document to the end → **ρ = 0.9406** (loud, for 99%-intact local order).
3. **v1's prototype `0.485` was an artefact of a global scramble.** Page-local scrambling on real
   prose scores ~0.98. `containment` (0.500 on half-dropped) and `inflation` (2.00 on duplicated)
   survive; the order figure did not.

Also fatal: `Coverage` measured the **control's** pages, so it was identical whether the candidate
kept every page or dropped half; *"exit non-zero but still write"* matched no semantic in this
codebase and broke retry; and anchor-resolution rate is biased toward the authoring backend **and
order-insensitive**, so a fully scrambled extraction scores 100%.

## Constraints (unchanged from v1, and still binding)

- **Zero runtime dependencies** — stdlib + poppler (`pdfinfo` ships with `pdftotext`).
- **Measure before building** — the rule v1 violated.
- **Prefer the loud default** — a check that degrades quietly is a defect generator.
- **Env, not config, for private endpoints** — `KLODE_DOCLING_URL` names an internal host and
  `library.toml` is tracked.
- **Backends stay lazy** — importing `klode.lib.formats` pulls in no backend.

---
---


The audit did not refine v1; it moved the foundation. v1 proposed a **gate** built on a control that
cannot arbitrate, scored by a metric at the wrong scale, with thresholds deferred to a measurement
scheduled *after* the gate. v2 inverts that: **build the measurement, gate nothing until it exists.**

That is the project's own rule (*measure before building*) applied to a plan that had violated it.

## The reframe

| v1 claim | v2 position |
|---|---|
| Control-vs-candidate agreement **verifies** integrity | It is **telemetry**, not a verdict. It detects disagreement; it cannot say which side is right. |
| Whole-book Spearman detects scrambling | Replaced by **page/window-local** order scoring. Global ρ misses per-page reversal (0.999978) and overreacts to a moved block (0.9406). |
| `Coverage` answers "did we get every page?" | It answers that for the **control**. Candidate coverage needs candidate **page provenance**, which the layout model's structured output may already carry. |
| Anchor-resolution rate decides the backend | Demoted to a **compatibility** statistic. It is biased toward the authoring backend and order-insensitive — a fully scrambled text scores 100%. |
| Ship a gate, calibrate later | **No gating until a labeled corpus exists.** A gate whose acceptance test cannot be written is not implementable. |

## Work items

**WI-0 · A labeled, page-level extraction corpus.** *Now the first item, not a later one.* Real PDFs
— born-digital, scanned, two-column, table-heavy, footnoted, mixed-language, broken text layer, blank
pages — with page-level ground truth from rendered pages, not from another extractor.
*Check:* `find tests/fixtures -iname '*.pdf'` currently returns **0**. Nothing downstream can be
validated until this exists. Licensing matters: prefer public-domain or synthetic-but-real PDFs that
can be committed.

**Status:** DONE — 2026-08-11
**Changed:** tests/fixtures/pdfs/make_fixtures.py, tests/fixtures/pdfs/*.pdf (5),
tests/fixtures/pdfs/GROUND-TRUTH.json, tests/test_pdf_corpus.py
**Verified:** python3 -m pytest tests/test_pdf_corpus.py -q (8 passed, 12 subtests)
**Note:** ground truth is true *by construction* — the generator places known text at known
coordinates, so no extractor labels the corpus. Covers page counting, a blank page, two-column
reading order, running heads, and the trailing-form-feed off-by-one. `not_covered` in
GROUND-TRUTH.json records the four gaps that still need real files (scans, a broken text layer,
non-Latin scripts, real table/footnote complexity) so this is not mistaken for a complete set.

**WI-1 · Agreement primitives, scoped honestly.** `containment` and `inflation` survive the audit
unchanged — they are algebra and they behaved correctly on both synthetic and real prose. Order
scoring becomes **page/window-local displacement** (per-window rank agreement, or matched-shingle
order), reported per window with a distribution, never a single book-level scalar.
*Check:* reversing every page must fail while whole-book page order is intact; moving one block must
not dominate. Both permutation tests, on real prose, with a stated tokenizer (case, punctuation,
hyphenation, Unicode normalization all fixed and tested — they change anchor counts materially).

**Status:** DONE — 2026-08-11
**Changed:** klode/lib/agreement.py, tests/test_agreement.py
**Verified:** python3 -m pytest tests/test_agreement.py -q (20 passed)
**Note:** both v1 failure numbers are pinned as regression tests against the OLD metric
(per-page reversal ρ=0.999978, relocated block ρ=0.9406). Window-local order inverts that
sensitivity: page reversal drives 10 of 10 windows to −1.0; a relocated block disturbs 1 of 10 and
leaves the median at 1.000. One clarification the tests forced: *"must not dominate"* is not *"must
not show"* — a moved block SHOULD register in the straddling window, and suppressing it would be a
blind spot. Tokenizer is stated and tested (NFKC, de-hyphenation matching `common._dehyphenate`,
casefold, non-alphanumeric split).

**WI-2 · Candidate page provenance.** Before rerunning a known-bad control, ask whether the layout
backend's structured result already carries page numbers / bounding boxes. If it does, candidate
coverage becomes direct rather than inferred.
*Check:* for 20 PDFs, measure declared-pages-represented, blocks lacking page provenance, duplicate
page assignments, and runtime cost. If the structured output cannot supply it, fall back to sampled
rendered-page OCR and compare cost against a second full extraction.

**Status:** DONE — 2026-08-11
**Changed:** klode/lib/coverage.py, klode/lib/formats/pdf.py, klode/lib/formats/_base.py,
tests/test_coverage.py
**Verified:** python3 -m pytest tests/test_coverage.py -q (16 passed, 10 subtests)
**Note:** the structured result DOES carry it — docling-serve is now asked for `md,json` and
`prov[].page_no` is read directly, so candidate coverage no longer depends on the control.
`Extraction.pages` is `None` (= *cannot say*) for every text-only backend, and the tests pin that
`None` never reads as "nothing missing". The defect is pinned directly: control coverage is
byte-identical whether the candidate kept every page or dropped half.
~~**Partial:** the plan's *"for 20 PDFs, measure ... runtime cost"* is **not run** — docling is not
installed and there is no endpoint here, so the parsing is proven against mocked structured
responses and the real-world measurement is carried to Outstanding work.~~
**Unblocked 2026-08-11 (later the same day):** a `docling-serve` endpoint became available, and the
structured path is now proven against a real server rather than a mock — a two-column PDF returned
`pages (1, 2)` with per-page text, resolved from `~/.klode/settings.toml` with no environment
variable set. The 20-PDF run is in `eval/extract_bakeoff.py` against a real corpus; its result is
recorded below under *Corpus measurement*.

**Also fixed while here:** the `auto` escalation path called the text-only `_docling`, so an
auto-escalated docling win arrived with `pages=None` even when the backend had supplied provenance
— coverage abstained on evidence it already had. Forced tiers had carried pages since round 1;
`auto` had not.

**WI-3 · Sampled visual ground truth.** The only thing that actually establishes fidelity: render N
random pages, OCR them, compare against the candidate's text for those pages. Record the seed and
the page numbers so the sample is reproducible and auditable.
*Check:* report per-failure recall and clean-document false-positive rate with intervals — not four
point values.

**Status:** DONE — 2026-08-11
**Changed:** klode/lib/visual.py, tests/test_visual.py
**Verified:** python3 -m pytest tests/test_visual.py -q (15 passed, real pdftoppm + tesseract)
**Note:** this is the one signal that breaks the circularity — the rendered page is downstream of
no extractor. Runs for real here: faithful candidate scores recall 1.0 on the corpus, wrong text
scores <0.3, a dropped page is recorded as an *error* rather than a 0.0 (those are different
findings), and the worst page is surfaced rather than averaged away. Seed and sampled page numbers
are recorded on every report. Recall, not F1, deliberately: extra candidate text (running heads,
hyphenation artefacts) is not evidence of damage.
**Partial:** *"false-positive rate with intervals"* is **not** produced — that is a statistic over
a labeled corpus with real-world failures, and WI-0 covers structural cases only (its `not_covered`
list names the gaps). Carried to Outstanding work.

**WI-4 · Verification states and transactional semantics.** Replacing v1's incoherent
"non-zero but still write":
- **default:** verification failure **refuses promotion to the shelf** and exits non-zero — matching
  `ingest.py`'s existing pre-write guards, which is the semantic already in this codebase;
- `--accept-unverified`: writes, exits **zero**, and persists `verification_status="unverified"`;
- states are `verified | unverified | abstained | failed`.
*Check:* an end-to-end test asserting **either** success + promoted artifact **or** failure + no
shelf mutation. Never both. (v1 failed this: the retry would hit an existing file and demand
`--force`.)

**Status:** DONE — 2026-08-11
**Changed:** klode/lib/integrity.py, klode/lib/ingest.py, klode/lib/cli.py,
tests/test_ingest_integrity.py
**Verified:** python3 -m pytest tests/test_ingest_integrity.py -q (17 passed)
**Note:** all four states implemented, with `abstained` explicitly NOT ok (`Integrity.ok` is true
only for `verified`) and explicitly NOT blocking — refusing every PDF we cannot measure would break
the tool on exactly the documents it is for. A measured failure raises before the write, so the
retry test passes with no `--force`: the first attempt left nothing behind. `--accept-unverified`
downgrades the state but **keeps the reasons and metrics**; an override that erases its own
evidence is worse than no check. CLI uses `BooleanOptionalAction` for `--verify/--no-verify`.
**Provisional:** thresholds (containment 0.80, inflation 0.67–1.50, median order 0.0) are loose on
purpose and are recorded in every verdict so records already written can be recalibrated. Tuning
them needs real-world failures the corpus does not yet have.

**WI-5 · Provenance bound to the persisted bytes.** Verification must describe **what was written**,
not what was extracted — v1 verified pre-normalization, but `normalize.process()` produces the shelf
artifact and can strip a legitimate repeated refrain as a running head. Record the final output
sha256, both backends' versions, metric-schema version, thresholds, decision, candidate coverage,
and status.
*Check:* changing one byte of the shelf source must invalidate the matching verification record; a
forced re-ingest must not inherit the previous verification. A card front-matter field is the wrong
home — `build.py` rewrites machine-managed front matter and would drop it.

**Status:** DONE — 2026-08-11
**Changed:** klode/lib/ingest.py (provenance row), tests/test_ingest_integrity.py
**Verified:** python3 -m pytest tests/test_ingest_integrity.py -q (17 passed)
**Note:** verification runs on the NORMALIZED text — the bytes actually written — with the control
put through the same `process()` pipeline, or it would not be a comparison. The row records
`output_sha256` of the shelf artifact plus schema, status, reasons, metrics, thresholds, and both
tiers. Changing one byte orphans the record; a forced re-ingest appends a NEW row rather than
amending. Front-matter was avoided exactly as the plan says: `build.py` would drop the field.

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

**Status:** DONE — 2026-08-11
**Changed:** klode/lib/settings.py, klode/lib/cli.py, klode/lib/ingest.py, klode/lib/opspec.py,
dev-docs/SPEC-operations.md, tests/test_settings.py
**Verified:** python3 -m pytest tests/test_settings.py -q (12 passed, 8 subtests); full suite 688
**Note:** all three defects closed. `--tier` and `--verify` now default to `None`, so an omitted
flag is distinguishable from an explicit one — pinned by a test that parses both forms. `klode
settings [--sources]` prints every effective value with its origin. The scope claim is narrowed in
the module docstring: this configures a judge *model* and ingest defaults, not "providers", because
there is exactly one transport. A test asserts no credential, endpoint, or URL can ever become a
settings key, and that `ANTHROPIC_API_KEY`/`KLODE_DOCLING_URL` stay environment-only.
**Also:** adding a CLI verb tripped the repo's own anti-drift guard (`test_parity`), which is the
guard working — `settings` is now registered in `opspec.py` and `SPEC-operations.md` as a CLI-only,
registry-scoped op with `mcp=()`: an agent has no business reading the operator's configuration.

**WI-7 · marker bake-off.** Unchanged in intent, corrected in metric: ranked by WI-1/WI-2/WI-3
against WI-0's ground truth. Anchor-resolution rate is reported as *compatibility*, never as the
decider.
*Check:* backend ranking must not reverse when anchors are chosen from a different backend's output.
If it does, the metric measured authoring compatibility, not fidelity.

**Status:** DONE — 2026-08-11
**Changed:** eval/extract_bakeoff.py, tests/test_bakeoff.py
**Verified:** python3 -m pytest tests/test_bakeoff.py -q (9 passed); harness runs against the real
corpus (5 PDFs, pdftotext visual fidelity 0.975–1.000)
**Note:** the plan's check was RUN, and the ranking does reverse — with anchors authored against
A, A wins; with anchors from B, B wins. A second test shows anchor resolution scoring 1.0 on fully
reversed text. Both are pinned, so the metric is reported as `compat` (a migration statistic) and
ranking is by `visual` — the only column not derived from another extractor. Absent backends are
named with a reason rather than silently omitted.
~~**Blocked:** the actual marker-vs-docling comparison cannot be run — neither is installed here and
docling additionally needs an endpoint or a heavy torch install. The harness is the deliverable;
the verdict on marker is carried to Outstanding work.~~

**Unblocked and RUN, 2026-08-11.** Both backends became reachable over HTTP. The comparison ran on
20 born-digital academic PDFs; the result is committed at
`eval/results/extract-bakeoff-2026-08-11.json` and summarised in `dev-docs/ingestion-toolchain.md`
§2.2. **docling keeps its tier-3 slot, earned on reading order** (median 1.000 vs pdftotext's 0.697
on two-column papers; recall is a tie). **marker does not earn one** — it failed 16 of 20 documents
on this deployment, so no paired basis to rank it exists.

**The run also caught the harness lying.** Its first aggregation ranked marker FIRST, reporting
`scored 4/4 pdfs` — because failed (document, tier) pairs were dropped from the report, so the
denominator counted only successes and 4-of-20 coverage read as complete. An audit found it, two
independent jobs reproduced it, and the ranking was re-derived only after the fix. The plan's own
premise — *a backend earns its slot by measuring better* — would have been satisfied by a number
that meant nothing.

## Known blind spot, accepted and recorded

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

## The assumption to keep watching

That agreement with `pdftotext` is evidence of fidelity to the **rendered** PDF — on the very path
where `pdftotext` was already classified unreliable. Until WI-3 puts rendered-page ground truth
underneath it, everything above is a disagreement detector. Useful, worth having, and not what the
opening section of this document asked for.
