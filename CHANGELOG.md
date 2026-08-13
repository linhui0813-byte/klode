# Changelog

Notable changes per release. Versions follow semantic versioning, with the pre-1.0 convention that
a **minor** bump may carry breaking changes.

## Unreleased

An audit of the whole repo, adversarially reviewed before any of it was written. Three findings
here came from that review rather than from the audit; two of my own proposed fixes were wrong and
were replaced after the checks meant to confirm them failed instead.

> ### ⚠️ Verdicts move, and stored calibrations stop claiming coverage.
>
> The gate compared a **rounded** score against the hurdle, so any draft within half a point below
> it was passed. A draft that scored Go at the boundary now scores **Recycle**. The error ran one
> way only — across two-criterion rubrics with maxima 2..10 there are twelve false-Go
> configurations and no false-Recycle — so no verdict moves from Recycle to Go.
>
> `Calibration` now pins the **judge** as well as the rubric. Existing records still load and
> report `calibrated=False` until re-measured, because a record measured through one model at
> `permutations=2` was being claimed by a different model running undebiased.

### Security

- **A public address spelled as an integer bypassed the plaintext-transport guard.**
  `http://134744072` resolves to `8.8.8.8` and was accepted: it carries no dot, so the single-label
  rule that exists to permit `http://docling` classified it as a container name before `ipaddress`
  was consulted. `klode ingest` uploads whole documents to that endpoint. No opt-in was required —
  the guard simply never fired. Classification is now by address, for any spelling, ahead of every
  name rule. Ambiguous dotted forms (`010.010.010.010`, octal to a resolver, refused by Python)
  still fail closed as public.
- **A shelf name could switch off the copyright-leak guard.** `git ls-files -z <paths>` had no
  `--`, so a shelf called `--others` was consumed as an option: git listed untracked files,
  returned 0, and the guard reported no leak with a copyrighted source tracked in the index.
  Closed at the call site and at the config boundary, which no longer accepts a shelf or
  `extra_guard_dirs` entry beginning with `-`. `--literal-pathspecs` also stops a shelf named
  `books*` dragging a sibling `booksOTHER/` into the report.

### Fixed

- **A glob metacharacter in a library path made the fail-closed linter pass green.**
  `glob.glob(os.path.join(cfg.cards, "*.md"))` reads `[ ] * ?` anywhere in the joined string,
  directory prefix included, so a KB under `.../kb[1]/` enumerated zero cards — and `unmeasured` is
  recorded only `if cards:`, so an empty list is the one shape that slips past the abstention
  guard. `klode check` exited **0** with `OK: 0 errors` over a citation resolving nowhere, and
  `klode build` rewrote `INDEX.md` to empty. A shelf name is a second injection point. Enumeration
  returning nothing while card files sit on disk is now a loud error naming itself as a bug.
- **An edited rubric could not be re-approved by the command its own error named.** `approve` and
  `repin` both ran the approval-digest check on the unmodified document, so the stale digest that
  sent you there was what refused you. Rewording one level descriptor bricked the rubric, with no
  documented way out. Both now demote before parsing; every reader still refuses an edited rubric.
- **The Go/Recycle threshold was applied to a rounded score.** See the warning above. The
  comparison is now exact and the displayed score is the floor — for an integer hurdle
  `floor(x) >= h` exactly when `x >= h`, so the number shown can never contradict the decision.
  `hurdle` must now be an integer, which that property depends on.
- **`judge.permutations` accepted odd counts that unbalanced the debiasing.** At 3 the split is two
  forward runs to one reversed, so the average keeps the position bias it exists to cancel, at 1.5×
  the cost of the 2 that would have cancelled it. The domain is now 1 or an even number to 16. The
  constructor also accepted `True` (which ran one silent forward pass), `2.0`, and 17.
- **The plaintext refusal prescribed `[ingest].allow_insecure_http`,** which is not a setting —
  writing it made the settings file fail to load. It could not have worked as a setting either, and
  a persisted value grants more than the variable does. The message now names
  `KLODE_ALLOW_INSECURE_HTTP` and says why it is deliberately not a setting.
- **Every release so far was published without the suite having run on it.** `tests.yml` triggers
  on `branches: ["**"]`, which does not match a tag ref, and `publish` needed only `build`.
  `workflow.yml` now calls `tests.yml`.
- **Four CLI hints named `lib`,** the tool klode was ported from — runnable only on the machine
  klode was written on.
- **`Config.load("path")` raised `AttributeError`** on a string, at the front door of the public
  facade, in the module that opens by promising not to fail confusingly. All three path parameters
  coerce now.
- **`klode.gate` printed 25 lines of stack for a bad `-c`** where `klode.lib` prints one line.

### Added

- `tests/fixtures/kb-fixture/library/frameworks/_criteria/mixed-scale.json` — the first fixture
  rubric whose criteria use **different** behavioral scales (0..3 and 0..7). Both existing rubrics
  are uniform, and a uniform rubric's mean always lands on an integer percentage, which is why the
  verdict-rounding defect was invisible to the whole suite. Checked by CI so it cannot drift back.
- `PROMPT_VERSION`, with a test that hashes `STEPS_PROMPT`/`FORM_PROMPT` and fails when either
  changes without the constant moving — a prompt edit silently invalidates every calibration
  measured through the old wording.

## 0.4.2 — 2026-08-13

Documentation only — no behaviour change. Cut as its own release because the thing it fixes is a
discoverability defect in the previous one: the warning below did not exist in any tagged artifact.

### Changed

- **The upgrade warning is now in the release that carries the change.** `v0.4.1` shipped the
  exit-code break, but documented it in the `0.4.0` section — and `0.4.0` was never tagged. Anyone
  upgrading from `0.3.0` read the `0.4.1` notes and never saw the one change likely to turn their
  CI red. A breaking change nobody can find is an undocumented breaking change. The `0.4.1` section
  now opens with it, and the GitHub release notes lead with it, because that is where release notes
  are actually read.
- **Remote layout backends are documented as the recommended path**, not a convenience for
  machines without a GPU. A remote conversion has a deadline that scales with the document; the
  in-process backends have no wall-clock bound at all, which is the known limit this project
  declined to close with an untestable worker process. Choosing the bounded path avoids the gap
  rather than papering over it, and keeps the client dependency-free — which is the project's own
  headline claim. Verified end to end on a machine with neither kreuzberg nor docling installed.

## 0.4.1 — 2026-08-13

> ### ⚠️ Upgrading from 0.3.0? Read the 0.4.0 section too.
>
> **0.4.0 was prepared but never published**, so `v0.4.1` is the first tag of this line and
> carries *everything* since `v0.3.0` — including the change most likely to break you, which is
> documented one section down rather than here:
>
> **Commands that could not do their work now exit 2 instead of 0.** If your CI runs `klode check`
> on a machine without the corpus installed, it will now fail. That is deliberate: `klode check
> --strict` used to print `OK: 0 errors` and exit 0 on a library whose citation-rot check never
> ran, and a fresh clone has no corpus — so every anchor could have rotted while the build stayed
> green. Pass `--allow-unmeasured` to accept the gap knowingly.
>
> The full table of moved exit codes, the card `file:` confinement, and the other breaking changes
> are in [0.4.0](#040--2026-08-12).

A second audit, of the code written to close the first one. 1,515 lines had been reviewed by
nobody but their author; an independent pass plus an owner-proxy review of three deferred decisions
returned 22 findings, five of which were rows the first audit had already marked `fixed`.

### Breaking

- **`klode review` verdict labels name the judge that actually ran.** The line was hardcoded to
  "stub judge", which would have stayed wrong the moment a real one was wired. Anything parsing
  that string must expect the judge's class name and a calibration reason.
- **`--max` requires `--grep`, and defaults to `None` rather than 10.** An explicit `--max 10` was
  indistinguishable from omission, so the dependency check could not tell "asked for and ignored"
  from "not asked for".
- **`--tier auto` refuses an extraction with too little text when no control corroborates it.**
  With `pdftotext` failed, a single clean token became the chosen result and cleared the corruption
  gate — a ratio scores one word exactly as it scores an empty string.

### Added

- **`klode review --live-judge`** — constructs the real `LLMJudge` from `[judge].model` and
  `[judge].permutations`. Those settings were declared, labelled "not yet consumed", and inert; an
  owner-proxy review ruled that a labelled dead setting is still dead. The flag exists because a
  config file is consent to *choose* a model, not to *spend money*: `ANTHROPIC_API_KEY` is commonly
  ambient for other tools, and this command advertises a stub. Without the flag klode makes no
  network call whatever the settings say. With it, a model and a key are required and the cost —
  `1 + permutations` calls per criterion — is printed before any call is made.
- **`klode settings --lint`** — validates every value in the settings file, including ones an
  override shadows. Resolution validates the winners; a shadowed broken value still goes live the
  moment the override is removed, so both matter and neither should cost the other.
- **`KLODE_ALLOW_INSECURE_HTTP`** — an explicit opt-in for plaintext endpoints a lexical rule
  cannot classify. The private-host check is a heuristic, and it now says so rather than guessing.

### Fixed

- **The bake-off dropped the wrong backend when pairing.** It removed the tier with the fewest
  total measurements, which is not the tier blocking the intersection: with A covering documents
  1–3, B covering 1–4 and C covering 4–7, A and B are perfectly comparable and the ranking came
  back empty. Selection is exhaustive now, and a test checks it against a brute-force oracle over
  every coverage mask.
- **Remote backends were still converted twice per document.** `_extract()` took the text and
  `_structured_pages()` ran the backend again for page text, so the two could describe different
  nondeterministic runs — `words` and `visual` describing different conversions. One invocation now
  returns both.
- **`--lang` reached one backend of four**, so a non-English scan was OCR'd as English while the
  CLI and the changelog both said otherwise. It now reaches xberg, docling (remote and local) and
  marker, asserted on the request body.
- **Terminal sanitisation was applied per call site and had already missed one** — `verify` printed
  raw source lines exactly as `zoom` had. The module now shadows `print`, so there is a single
  boundary, with an AST guard against reaching around it. Writing that guard found the sanitiser
  stripping newlines, which would have collapsed every multi-line message into one line.
- **`--json kbs` exited 1 on a fresh install** while `klode kbs` exited 0. An empty catalog is a
  successful answer; an empty search result is a miss. Both are empty lists, so the operation has
  to say which.
- **Four guards added in 0.4.0 refused legitimate input**: `http://docling:15001` (the ordinary
  Docker deployment), an empty *optional* environment variable, a shadowed file value blocking
  unrelated commands, and a single ordinary identifier such as `testIDs` in a short document.
- The upload cap raced between `stat()` and `read_bytes()`; the `init` symlink guard covered three
  managed paths of eight; the PDF header check allocated whole files to read five bytes; a
  kreuzberg `TypeError` inside extraction was misdiagnosed as API incompatibility; an xberg
  `ImportError` erased the reason `pdftotext` had failed.
- **Conversion deadlines scale with the document.** A 222-page scanned book exceeded both fixed
  timeouts, so it could not be ingested through a remote backend at all, while a 12-page paper
  converts in under three seconds. One constant cannot serve both.

### Known limit, stated rather than implied

Local OCR (in-process kreuzberg and docling) has **no wall-clock bound**; `pdftotext` and the
remote backends do. An `OCR_TIMEOUT` constant previously sat in the source wired to nothing, which
reads as a guarantee. Bounding these needs a worker process — they are C/torch extensions that
never return to the interpreter to notice a signal — and that re-imports torch in the child,
roughly doubling docling's cost. It was built, and not shipped: neither backend is installed in the
environment where it was written, so it could not be tested against the thing it wraps. Use a
remote endpoint where a deadline matters.

## 0.4.0 — 2026-08-12

Extraction integrity: an ingest can now say whether the text it wrote actually represents the
document, and refuse the shelf when it does not. Plus a settings file, and two remote layout
backends reachable from it.

### Breaking

- 🔴 **Commands that could not do their work now exit 2 instead of 0.** This will turn a green CI
  red, and that is the point: each of these previously certified something that had not happened.
  **If your CI runs `klode check` on a machine without the corpus installed, it will now fail.**

  | command | condition | was | now |
  |---|---|---:|---:|
  | `check` | corpus absent, or ANY card's source not installed | `OK`, 0 | `ABSTAINED`, 2 |
  | `check --entail` | the entailment backend could not load | `OK`, 0 | `ABSTAINED`, 2 |
  | `build` | nothing to build | 0 | 2 |
  | `normalize --check` | no file matched, or files were unreadable | 0 | 2 |
  | `zoom --level content` | the source is not installed | 0 | 2 |
  | `eval/extract_bakeoff.py` | nothing could be ranked | 0 | 2 |

  The convention is now uniform: **1 = measured and failed · 2 = could not measure · 0 = the work
  happened.** Pass `--allow-unmeasured` to `check`, `build`, or `normalize` to accept a gap
  knowingly — which is the difference between an accepted limitation and a silent one.

  Why this is worth breaking: `klode check --strict` printed `OK: 0 errors` and exited 0 on a
  library whose citation-rot check never ran. A fresh clone has no corpus (it is git-ignored), so
  every anchor could have rotted and the build stayed green. CI reads exit codes, not notes.

- 🔴 **A card's `file:` is confined to `<lib>/<shelf>/<name>.txt`.** It was confined only to the
  library root, so a card could point at `library/.env`, `library.toml`, or `books/../.env` and
  `zoom --grep` would print the matching lines. Cards travel — the registry exists so klode can be
  pointed at someone else's knowledge base — so that field is untrusted input. Any card whose
  source sits outside a configured shelf now reports "not installed" instead of being read.

- **`normalize(stamp=...)` rejects anything but one safe path component.** `../../tmp/x` escaped
  the backup root, where copies of a copyrighted corpus land.

- **`klode init` replaces its managed `.gitignore` block rather than appending it once.** A new
  shelf's copyrighted `.txt`/`.pdf` previously stayed unignored on re-init. Rules outside the
  `# >>> klode managed` markers are preserved untouched.

- **`klode ingest` refuses to promote a measured integrity FAILURE.** A candidate that drops,
  duplicates, or scrambles material relative to the control raises `VerificationError` and writes
  nothing — no shelf source, no provenance row. Override with `--accept-unverified`, which records
  the finding as `unverified` rather than erasing it. Skip the check with `--no-verify`.
- **`abstained` is not `verified`.** Verification has four states, and `Integrity.ok` is true only
  for `verified`. Anything reading "no failure reported" as success is now wrong on purpose:
  abstention means *could not measure*, and it is the honest answer for a document with no control,
  too few anchors, or no rendering tools installed.
- **Settings-backed CLI flags default to `None`, not to their value.** `--tier` no longer defaults
  to `"auto"`, so `klode ingest x` and `klode ingest x --tier auto` produce different namespaces.
  With a value default the argument level of the precedence chain silently swallowed environment,
  file, and default. Anything inspecting `args.tier` must handle `None`.

### Added

- **Extraction integrity** — three independent measurements that fail differently: `containment`
  (dropped material), `inflation` (duplication), and per-page rank correlation (reading order).
  Order is measured **per page**, using real page boundaries when the control supplies them: a
  whole-book correlation scores 300 pages with *every page internally reversed* at ρ = 0.999978,
  which is invisible. Both numbers are reproduced in `tests/test_agreement.py`.
- **Visual ground truth** (`klode.lib.visual`) — renders sampled pages with `pdftoppm`, reads them
  with `tesseract`, and compares against the candidate. The only signal here not downstream of
  another extractor. Sampled, so the seed and page numbers are recorded; both binaries optional,
  and absent they abstain loudly.
- **Page coverage** (`klode.lib.coverage`) — declared (`pdfinfo`) vs control (form feeds) vs
  candidate (structured `prov[].page_no`). Candidate coverage is direct or `None`, never inferred.
- **Provenance bound to the bytes written.** Every row carries `output_sha256` of the exact file,
  the verdict, its metrics, and the thresholds that judged them, so a later recalibration can be
  applied to records already written. Changing one byte orphans the record.
- **`~/.klode/settings.toml`** — argument → environment → file → default, with the winning source
  recorded and printed by `klode settings`. Unknown keys, wrong types, and out-of-domain values are
  rejected loudly rather than ignored. Credentials are never settings: `ANTHROPIC_API_KEY` stays
  environment-only, and a test enforces the ban.
- **Remote layout backends.** `[ingest].docling_url` and `[ingest].marker_url` (or
  `$KLODE_DOCLING_URL` / `$KLODE_MARKER_URL`) point at a `docling-serve` or `marker_server`
  endpoint — the GPU runs server-side, so klode keeps zero runtime dependencies. `--tier marker`
  joins `--tier docling`. **`marker` is not in the `auto` escalation ladder**; docling is, and has
  now earned that place by measurement — median reading order 1.000 against pdftotext's 0.697 on
  20 two-column papers (`eval/results/extract-bakeoff-2026-08-11.json`). marker failed 16 of those
  20 documents on the deployment tested, so no paired basis to rank it exists. A backend earns a
  ladder slot by measuring better than the one it would displace, and `eval/extract_bakeoff.py` is
  what decides that.
- **`eval/extract_bakeoff.py`** — ranks backends by fidelity to the *rendered page*. Anchor
  resolution is reported as a migration statistic and never ranked on: it is biased toward whichever
  backend authored the anchors and blind to reading order, and `tests/test_bakeoff.py` demonstrates
  the ranking reversing when the anchors change origin.
- **`klode settings --explain`** — describes every setting: what it does, its allowed values, its
  environment variable, and its built-in default. Each `Spec` already carried that text and nothing
  printed it, so a setting nobody could discover did not exist for them.
- **A committed backend measurement** (`eval/results/extract-bakeoff-2026-08-11.json`) — 20 real
  academic PDFs. docling keeps its tier-3 slot on reading order (median 1.000 vs pdftotext's 0.697
  on two-column papers; recall is a tie). marker does not earn one: it failed 16 of 20 documents on
  the deployment tested, so no paired basis to rank it exists.
- **A labeled PDF corpus** (`tests/fixtures/pdfs/`) — hand-built, so ground truth is true by
  construction rather than by another extractor's say-so, byte-reproducible from its generator, and
  `GROUND-TRUTH.json` names what it does **not** cover.

### Fixed

- **A confident `verified` on unmeasured evidence**, in every form found: NaN metrics (which make
  every threshold comparison false), order measured on only a minority of windows, an unknown
  declared page count, and coverage that could not speak for the candidate. All now abstain.
- **`order_median` was not a median** — a nearest-rank quantile returned one of the two middle
  observations, reporting 1.0 for `[-1.0, 1.0]`. Same defect in the bake-off's `median_visual`.
- **A one-page-in-ten reversal passed a median-only gate.** The worst window is now gated too.
- **Non-ASCII text was discarded by the tokenizer**, so two unrelated CJK or Cyrillic documents
  scored identical and verified.
- **Ingest was not transactional in either direction.** Promoting before recording left a shelf
  artifact when the provenance log was unwritable; recording before promoting left a row for an
  artifact that never landed. Every fallible step now runs before either side is mutated.
- **A predictable `<dest>.tmp` name** was followed through a planted symlink, truncating the target.
- **The bake-off could crown a backend it had measured on half the corpus.** Failed
  (document, tier) pairs were dropped from the report, so the denominator counted only successes
  and a backend scored on 4 of 20 documents was printed as "scored 4/4" and ranked first. Ranking
  is now a paired comparison over the documents the ranked backends share, and refuses rather than
  ordering on an insufficient basis. This was caught on a real run, where it had ranked marker
  first.
- **`--resume` merged incompatible experiments** — it compared only seed and sample size, so adding
  a tier or editing a PDF silently mixed old measurements with new. It now validates a manifest
  (tiers, sample, seed, per-document content hashes, anchor hash) and refuses a mismatch, a missing
  checkpoint, or a corrupt one, instead of silently restarting and overwriting the evidence.
- **A one-word OCR result could displace a whole document.** It scores corruption 0.0 — there are
  no corruption markers in one word to find — so it looked cleaner than 320 garbled words.
  `corruption_score` is a ratio and cannot see loss, so candidates must also retain a share of the
  incumbent's words.
- **Endpoint URLs were validated by string prefix**, so `http://user:password@host` was accepted —
  a credential in `settings.toml` and therefore in every backup. Endpoints are now parsed
  structurally; userinfo, query, fragment, missing hosts, bad ports, and control characters are all
  refused, on every source.
- **The control was normalized page-at-a-time**, a different pipeline from the candidate's: a
  running head repeated on six pages is stripped from a whole document and kept on every page in
  isolation, so a faithful extraction lost containment.

<!-- the remainder of the same audit, released together -->

### Breaking

- **`--tier auto` now REFUSES a result that meets none of auto's own criteria.** It was
  returning `best` even when every tier failed the corruption threshold it judges by, and
  verification then abstained (the control tier IS pdftotext), so garbage was promoted with no
  check having run. Force a tier to accept such text deliberately.
- **An explicitly empty `KLODE_*` variable is an error, not absence.** `FOO=$TYPO klode …` fell
  through to the default while the operator believed the override was in force. Use `env -u`.
- **Plaintext `http://` endpoints are allowed only to a private destination** (loopback, RFC1918,
  tailnet 100.64/10, or a reserved name). klode uploads whole documents to them.
- **`--json` on a command that does not implement it now exits 2** instead of printing prose,
  which a machine consumer reads as valid output.
- **`--limit`/`--max` reject values below 1**; `--apply` and `--check` are mutually exclusive;
  `--grep`/`--max` are refused where they would be silently ignored; `--entail-model` and
  `--entail-threshold` require `--entail`.

### Fixed

- **Terminal control sequences from corpus and card text** were printed verbatim, so a source line
  could clear the reader's screen or set the window title. Prose output is sanitised; JSON is not,
  because a machine consumer wants the real bytes.
- **`init` wrote THROUGH a pre-existing symlink** at `library`, `library.toml`, or `.gitignore`.
- **`_json_exit` inferred success from Python container types**, so `zoom --level full` on a card
  with no Full section exited 0 under `--json` and 1 in prose.
- **Prose `zoom --grep` bypassed the shared service**, skipping the stamped-source freshness and
  ambiguity checks. A changed source is now reported as stale rather than as citation rot.
- **An unknown settings key read as "unset"**, a missing explicit settings path read as "defaults",
  an empty typo'd `[section]` was dropped, and file values were validated only when they won.
- **The bake-off's median-of-medians** could hide a backend corrupting half its pages; the worst
  document and the full distribution now survive aggregation. One global seed gave every
  equal-length document identical page positions.
- Marker page-id collisions, unvalidated nested response fields, an unbounded upload body, a
  blanket `except Exception` around docling's OCR options, and `--lang` accepted by every extractor
  and honoured by none — it now reaches xberg, docling (remote and local) and marker, so a
  non-English scan is no longer silently OCR'd as English.

### Testing

Roughly thirty tests that asserted less than their names claimed: a denylist standing in for a
zero-dependency check, a "real poppler integration" that converted any failure into a skip, a
marker-not-in-`auto` gate implemented as a source-text search, a parity gate comparing only
subcommand names, and assertions on echoed inputs rather than on what the code under test received.

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
