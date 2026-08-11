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
