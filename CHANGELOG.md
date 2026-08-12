# Changelog

Notable changes per release. Versions follow semantic versioning, with the pre-1.0 convention that
a **minor** bump may carry breaking changes.

## Unreleased

### Added

- **Cited-passage retrieval** — `klode evidence`, MCP `retrieve_evidence`, and
  `klode.lib.retrieve_evidence` return verbatim L3 excerpts with card id, source path, line range,
  live SHA-256, and the retrieval route.
- **Fail-closed full-source fallback** — relevant card anchors are tried first; when they yield no
  usable passage, Klode searches the complete installed raw source automatically. Callers that
  judge card evidence insufficient can force the fallback with `--full-text` / `full_text=true`.
- **Honest terminal status** — an exhausted fallback returns `INSUFFICIENT_EVIDENCE` and explicitly
  says not to answer from recall. `EVIDENCE_FOUND` remains retrieval-only, never an entailment claim.

## 0.3.0 — 2026-08-09

The supervising gate (`klode.gate`) gets a real rubric artifact and a real judge. The engine
(`klode.lib`) gains a corpus-pinning primitive and closes two anchor-resolution fail-opens.

### Breaking

- **`gate.review_draft` now requires an authored `CriterionSpec`.** It no longer builds its rubric
  by promoting a synthesis's bold Craft bullets into positional `C1`/`C2` criteria. A dimension with
  no `_criteria/<dimension>.json` raises `SpecError` naming the authoring step. Craft-move loading
  survives as `load_criteria`, now the *seed* for authoring rather than the gate's input.
  Migrate: `python3 -m klode.gate -c LIB derive <dimension>`, fill it, then `approve`.
- **`criticality: advisory` is rejected** rather than accepted and ignored. The gate weights and
  gates every criterion identically, so the label had no behaviour; accepting it was a lie.
- **`[frameworks].criteria` must resolve inside the frameworks dir.** An absolute or `../` value now
  raises `ConfigError` instead of silently relocating the gate's root of trust.
- **A bare anchor now reports ambiguity whatever its match engine.** A `grep-re:` pattern, or a
  literal pinned only by `before`/`after`, previously never reported ambiguity. Anchors that
  resolved in several places may now surface as ambiguous under `klode check --strict` and will not
  ground a criterion. Pin them with `before:`/`after:`/`#n`.

### Added

- **CriterionSpec v1** (`klode.criterion-spec/v1`) — the gate's sole input. Field-level epistemics
  (`explicit` / `paraphrase` / `derived` / `operator_policy` / `unknown`), behaviorally anchored
  0..N levels, a computed corpus fingerprint, stable non-positional ids, and a `human_approved`
  admission gate bound to the rubric body by `approved_digest`. See `dev-docs/SPEC-criterion.md`.
- **Authoring CLI** — `python3 -m klode.gate {derive,check,approve,repin}`. `derive` seeds a
  candidate that deliberately does not validate: it never invents a warrant or a level descriptor,
  so the validator's errors are the author's worklist.
- **`LLMJudge`** — G-Eval two-step form-filling (steps derived before the draft is in view),
  balanced permutation over reversed level orders against position bias, injectable transport,
  stdlib-only. Malformed model output raises `JudgeError` rather than defaulting to a score.
- **Calibration gating** — `Calibration` is a measured record (rubric digest, `n`, kappa against
  human scores), not a flag. `Verdict.calibrated` drives `non_production` in the review service,
  which was previously hardcoded. A calibration measured on a different rubric does not transfer.
- **`lib.source_digest(cfg, card)`** — the live sha256 of a card's source, so a derived artifact can
  pin corpus state and detect drift.
- **`gate.ground_bindings`** — grounds each anchor against the card it was *declared* against.
- **`gate.rubric_identity` / `gate.canonical_digest`** — what a rating sheet and a calibration are
  measured against.
- **`eval/rate.py`** — inter-rater agreement (quadratic-weighted kappa, per criterion) as a rubric's
  acceptance test. Fail-closed: refuses same-rater comparison, mismatched rubrics, partial sheets,
  and an undefined kappa.
- **Test CI** — the suite, the fixture linters, the gate demo, and a zero-runtime-dependency probe
  now run on every push and PR, across Python 3.11–3.14, installing nothing.

### Fixed

- **Anchor ambiguity fail-open** (see Breaking). `grep-re: .*` previously grounded arbitrary text as
  a clean, unambiguous hit.
- **Citations could migrate across the panel.** A rubric citation declared to one card could
  silently resolve in another and score `Go`. Each anchor is now verified only against its declared
  card.
- **`explicit` was not verbatim.** It accepted `FOLDED_ONLY`, so the source `re-sign` satisfied the
  value `resign` — the opposite meaning — via hyphenation folding. It now requires a raw-line
  occurrence.
- **Folded matches could cite the wrong occurrence.** `_locate_folded` ignored `before`/`after`/`#n`
  and could show a judge an unrelated earlier line; it now honours the selector or reports the span
  unusable.
- **A running MCP server cached the registry at startup**, so moving or adding a KB left it serving
  a path that no longer existed until restart. The pool now re-reads the manifest when it changes.
- **Verdict rounding** — per-criterion percentages were rounded before averaging, which could flip
  Recycle to Go at the hurdle. Exact fractions are averaged and rounded once.
- **`eval/retrieval.py` reported `0.000` silently** against a corpus its gold set was not labelled
  for. It now exits non-zero naming the mismatch.
- **Path traversal** — a `dimension` reaching the gate from CLI or service params could resolve
  outside the criteria directory, and the authoring tool would write there.
- Numerous parser hardening fixes so malformed rubric JSON raises `SpecError` rather than
  `TypeError`/`AttributeError`/`UnicodeEncodeError`. See `.cc-suite/audits/` for the full record
  (48 findings across three audit rounds; 47 fixed, 1 refused with a recorded rationale).

### Documentation

- `README` states the guarantee's boundary: the linter proves a citation is **referential**, not
  that the quote supports the claim. Green `klode check` means "no citation rot," never "this is
  true."
- `dev-docs/SPEC.md` no longer documents commands that do not exist (`lib check` → `klode check`,
  `import lodlib` → `import klode.lib`).
- `dev-docs/gate-README.md` rewritten; it still described a walking skeleton.

### Known gaps

- **No rubric has been calibrated against human raters.** Every verdict the gate issues is therefore
  marked non-production — by mechanism, not by convention. This is the blocking item before any
  verdict should be trusted, and it needs people, not code.
- Cooper's **must-meet knockouts** (a single No → Kill) and Hold are not implemented; only
  should-meet scoring exists.

## 0.2.2 and earlier

Released before this changelog was kept. See the git history and `dev-docs/` for the design record.
