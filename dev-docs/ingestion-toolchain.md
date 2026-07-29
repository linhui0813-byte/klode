# The ingestion toolchain: `lib ingest`, the tiered pipeline, and provenance

**Date:** 2026-07-22
**Scope:** How a source PDF becomes a clean, grep-anchorable `.txt` on a shelf — the
`lib ingest` command, the three-tier extractor, the corruption-driven auto-selector, and the
`PROVENANCE.jsonl` ledger. Written to be read cold by someone who has never seen the pipeline.

This is the *cause* fix for the source-quality problem. The citation guard (`lib check`) proves a
quoted phrase still occurs in its source; it cannot prove the source text is *right*. Garbage in →
garbage anchored. Ingestion is where quality is won or lost.

---

## 1. The problem it solves

A library source is a plain `.txt` extracted from a PDF. The extraction method decides everything
downstream: if the PDF was OCR'd badly and the extractor trusts that bad text layer, you get
`regulatIOn of narrative mfonnafion` where the book says "regulation of narrative information" — and
every citation built on that passage is fragile or unanchorable.

Three failure classes, discovered empirically on real books:

| Class | Symptom | Example |
|-------|---------|---------|
| **Bad prior extraction of a good text layer** | `~`-in-word, mid-word caps | genette: `t~e`, `mfonnafion` |
| **Scanned page + bad baked-in OCR layer** | same garbage, `HiddenHorzOCR` font | gerrig |
| **Page furniture** | page numbers, running heads, OCR noise between pages | every book |

The first two need a *converter* decision; the third needs *cleaning*. `lib ingest` handles both,
picking the cheapest tool that reaches quality.

---

## 2. The three tiers

```
Tier 1  pdftotext -layout   (poppler · free · subprocess)  — PDFs with a usable text layer
Tier 2  xberg / kreuzberg   (Rust core + tesseract OCR)    — scanned prose, bad/no text layer
Tier 3  docling             (layout models · torch)        — complex multi-column / table docs
```

- **Tier 1 — pdftotext -layout.** A poppler system binary called over subprocess. Free, instant, no
  Python dependency. Correct whenever the PDF has a real, decently-encoded text layer — which is most
  born-digital PDFs *and* many scans whose OCR layer happens to be clean.
- **Tier 2 — xberg (PyPI `kreuzberg`).** A Rust-core document-intelligence framework using Tesseract
  for OCR. Re-OCRs the page images, ignoring a bad text layer. Fast (~0.35 s/page), light (~80 MB
  env). This is the workhorse for scanned prose books.
- **Tier 3 — docling (IBM).** Model-based layout reconstruction (torch + OCR models, ~1.2 GB). Its
  strength is *structure* — multi-column scientific papers, tables, figures, reading-order
  reconstruction. **Opt-in only** (`--tier docling`); it is the wrong tool for plain prose (see §4).

---

## 3. Auto-selection — decide by *measured* corruption, not by guesswork

`--tier auto` (the default) never guesses from the file. It extracts with the cheap tier, **scores
the result**, and escalates only if the score says the text layer is garbage.

```
flowchart TD
  PDF["source PDF"] --> T1["Tier 1: pdftotext -layout"]
  T1 --> SCORE{"corruption under 5 per 10k<br/>and words over 200?"}
  SCORE -->|"yes: text layer clean"| USE1["use pdftotext text"]
  SCORE -->|"no: garbled or scanned"| T2["Tier 2: xberg tesseract OCR"]
  T2 --> BETTER{"cleaner than pdftotext?"}
  BETTER -->|yes| USE2["use xberg text"]
  BETTER -->|no| USE1
  T3["Tier 3: docling opt-in<br/>complex layout only"] -.-> STRIP
  USE1 --> STRIP["strip page furniture"]
  USE2 --> STRIP
  STRIP --> OUT["write shelf/id.txt<br/>plus PROVENANCE.jsonl"]
```

Constants (in `lodlib/ingest.py`):

| Constant | Value | Meaning |
|----------|-------|---------|
| `CLEAN_THRESHOLD` | `5.0` | corruption/10k below which the text layer is trusted |
| `MIN_WORDS` | `200` | guard: an empty-but-"clean" extraction is not a win, escalate |

**Why measured, not font-based.** `pdffonts` can *hint* at a scan (`HiddenHorzOCR`, `GlyphLessFont`),
but the font is not decisive: barthes S/Z shows `GlyphLessFont` yet its OCR layer is clean (pdftotext
scores 0.1) — free Tier-1 wins. gerrig shows `HiddenHorzOCR` and its layer is garbage (20.7) — must
re-OCR. Only the corruption score separates them reliably.

### The corruption metric

`corruption_score(text)` = (tilde-in-word + mid-word-caps) per 10 000 words:

- **tilde-in-word** — `[A-Za-z]+~[A-Za-z]+` catches `t~e`, `Dist~nce` (a distinctive bad-OCR artifact).
- **mid-word-caps** — `\b[a-z]{2,}[A-Z]{2}[a-z]*\b` catches `mfonnafion`, `regulatIOn`.

Empirically clean sources score 0–4; garbled ones score 12–51. The 5.0 threshold sits in the gap.
It is a cheap heuristic, not a proof of accuracy — clean-looking OCR can still be wrong — but it
reliably separates *usable* from *garbage*, which is the decision the selector must make.

---

## 4. Why xberg beats docling for prose (the benchmark that set the default)

Head-to-head on a gerrig 8-page sample, forced OCR:

| Tool | corruption/10k | speed (8 pp) | env size | readability |
|------|---------------:|-------------:|---------:|-------------|
| pdftotext (bad OCR layer) | 18.1 | — | — | baseline garbage |
| **xberg (tesseract)** | **0.0** | **2.8 s** | 81 MB | clean, correct order |
| docling (default RapidOCR) | 18.3 | 117 s | 1.2 GB | garbage — Chinese OCR model on English |
| docling (tesseract backend) | 18.3 | 13.5 s | 1.2 GB | reading-order **scrambled** by the layout model |

Two independent findings:

1. **docling's default OCR backend is RapidOCR with a Chinese model** — applied to English it
   produces nonsense (`pavticiyatovy`, `Orhello`). Must be reconfigured for English.
2. **Even with the tesseract backend, docling's layout model over-processes plain prose** and
   scrambles reading order (fragments individually OK, sequence wrong).

Conclusion — *right tool for the job*, not "docling bad": docling's layout intelligence is for
structurally complex documents; on a scanned prose book it is a liability. For a book/paper library,
**Tier 2 (xberg) is the OCR default; docling stays opt-in** for the rare complex-layout source.

---

## 5. Usage

```bash
lib ingest <pdf> --shelf <shelf> [--id <stem>] [--tier auto|pdftotext|xberg|docling] [--lang eng] [--force]
```

| Flag | Meaning |
|------|---------|
| `--shelf` | target shelf (must be a configured `[library].shelves` entry) — **required** |
| `--id` | card/source stem (default: a slug of the filename) |
| `--tier` | `auto` (default) or force a tier |
| `--lang` | OCR language for Tier 2/3 (default `eng`) |
| `--force` | overwrite an existing shelf source |

Example (auto picks the cheapest correct tier and records provenance):

```bash
lib -c ~/…/doxai/library.toml ingest "~/Downloads/Some Book.pdf" --shelf narratology
# → tier: pdftotext (text layer clean) · corruption 0.25 → 0.25 · 66 furniture lines · provenance written
```

### The full ingest → card workflow

`lib ingest` produces the *source*; the *card* is still hand/agent work (never auto-summarized —
that is the drift the library exists to prevent):

1. `lib ingest <pdf> --shelf <s>` — clean text onto the shelf + provenance.
2. Add a `BIBLIOGRAPHY.md` row for the source.
3. `lib build` — scaffolds the card (L0) and refreshes `INDEX.md`.
4. Write the **grep-anchored** Thin (L1) / Full (L2) by hand, quoting real phrases from the `.txt`;
   set `zoom: full` and `aliases:`.
5. `lib check` — every anchor must resolve.

---

## 6. Provenance — `<lib>/PROVENANCE.jsonl`

Every ingest appends one JSON line. This is the "source identity / lockfile" the design owed: it
makes each source reproducible and its origin auditable.

```json
{
  "id": "gerrig-experiencing-narrative-worlds",
  "shelf": "science",
  "source_pdf": "richard-gerrig-experiencing-narrative-worlds-westview-press-1999.pdf",
  "source_sha256": "3af60f3425…",
  "tier": "xberg",
  "tool": "kreuzberg 4.10.2",
  "words": 83997,
  "corruption_before": 21.1,
  "corruption_after": 0.71,
  "furniture_stripped": 250,
  "ingested_at": "2026-07-22T…Z",
  "backfilled": true
}
```

| Field | Purpose |
|-------|---------|
| `source_sha256` | pins the exact PDF a source came from → detect a swapped/updated original later |
| `tier` / `tool` | how the text was produced → reproducibility, and which tool version to blame |
| `corruption_before/after` | extraction quality + the effect of furniture-strip |
| `backfilled` | `true` marks records reconstructed after the fact, not emitted by the tool at ingest time |

`PROVENANCE.jsonl` is **derived metadata, not copyrighted text — it is git-tracked** (unlike the
sources), greppable, and diffable, consistent with lodlib's file-based ethos. It is not yet consumed
by `lib check`; wiring a source-hash gate into the guard (fail if a source changed under existing
cards) is the natural next step (see §10).

---

## 7. Architecture — the core stays zero-dependency

lodlib's identity is stdlib-only, zero-dependency, file-based. The ingestion tiers must not poison
that. They do not:

- **Tier 1** shells out to the `pdftotext` **binary** — a system dependency, not a Python one.
- **Tier 2/3** are **lazy-imported** inside their extractor functions. `import lodlib` /
  `import lodlib.ingest` never triggers a kreuzberg or docling import. Absent the deps, `lib` and
  every other command still run; only `lib ingest` at a tier that needs them errors — with an install
  hint, not an ImportError traceback.

So the dependency graph is: **lodlib core = zero deps**; **`lib ingest` Tier 2 = optional kreuzberg**;
**Tier 3 = optional docling**. The heavy tools are opt-in extras injected into the tool's environment,
never runtime requirements of the library.

---

## 8. Setup

System binaries (Homebrew on macOS):

```bash
brew install poppler      # pdftotext, pdffonts — Tier 1 + diagnostics
brew install tesseract    # OCR engine used by Tier 2 (and Tier 3 if configured)
```

Python extras, injected into the lodlib tool environment (installed via `pipx`):

```bash
pipx inject lodlib kreuzberg   # Tier 2 (xberg) — ~80 MB, do this so `lib ingest` OCR works out of the box
pipx inject lodlib docling     # Tier 3 — heavy (~1.2 GB, torch + models); only if you need complex-layout OCR
```

If a tier's dependency is missing, `lib ingest` at that tier prints the exact inject command.

---

## 9. Diagnosing a PDF by hand

When you want to understand a source before ingesting:

```bash
pdfinfo  book.pdf | grep Pages                       # page count
pdffonts book.pdf | head                             # HiddenHorzOCR / GlyphLessFont hint a scan
pdftotext -layout book.pdf - | \                     # extract and score
  python3 -c 'import sys,re; t=sys.stdin.read(); w=len(t.split())or 1; \
    print("corruption/10k =", (len(re.findall(r"[A-Za-z]+~[A-Za-z]+",t))+ \
    len(re.findall(r"\b[a-z]{2,}[A-Z]{2}[a-z]*\b",t)))/w*10000)'
```

Score < 5 → Tier 1 suffices (free). Score ≥ 5 → the text layer is garbage; Tier 2 OCR. `lib ingest
--tier auto` does exactly this automatically.

---

## 10. Failure modes & troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `pdftotext not found` | poppler missing | `brew install poppler` |
| `xberg/kreuzberg not installed` | Tier 2 dep absent | `pipx inject lodlib kreuzberg` |
| xberg output empty/garbled | tesseract binary missing or wrong `--lang` | `brew install tesseract`; check `--lang` |
| auto stays on pdftotext despite garbage | pdftotext scored below threshold on a partial layer | force `--tier xberg` |
| tesseract "LEAK" warnings on stderr | tessdata cache teardown noise | harmless, ignore |
| `<dest> exists` | source already on the shelf | `--force` to overwrite (a backup path is your call) |
| ingested source breaks an existing anchor | new extraction wraps/renders a quote differently | `lib check` catches it; re-point the anchor or fix the passage |

Re-ingesting an existing source **replaces** its `.txt`; anchors may render differently (line
wrapping, hyphenation). Always `lib check` after, and verify the affected card's anchors resolve —
the de-hyphenation folding in the matcher handles the common `informa-\ntion` line-break case.

---

## 11. Case studies (this corpus, 2026-07)

| Source | PDF nature | tier | corruption before → after |
|--------|-----------|------|--------------------------:|
| genette — Narrative Discourse | text layer, bad prior extraction | pdftotext | 12.9 → 0.2 |
| barthes — S/Z | `GlyphLessFont`, clean OCR layer | pdftotext | 51.1 → 0.2 |
| gerrig — Experiencing Narrative Worlds | `HiddenHorzOCR`, scanned + bad OCR | **xberg** | 21.1 → 0.7 |
| barthes — The Pleasure of the Text (new) | clean OCR layer | pdftotext | 0.0 → 0.0 |

Three of four were rescued for free by Tier 1; only the genuinely scanned gerrig needed Tier 2. This
is the tiered pipeline's whole thesis: cheap path first, heavy tool only where measurement demands it.

---

## 12. Future work

- **Wire provenance into `lib check`.** A `[provenance]` gate: recompute each source's sha256 and
  fail if it changed under existing cards (the source-identity check the guard still lacks).
- **`lib ingest --build`** convenience flag to chain `lib build` after ingest.
- **Batch ingest** a directory of PDFs with a per-file tier report.
- Unrelated but tracked in `knowledge-base-research-conclusion.md`: the regex-fail-open in the
  citation matcher (needs an opt-in `grep-re:` marker + migrating the handful of intentional-regex
  anchors).

---

## Appendix — file map

| File | Role |
|------|------|
| `lodlib/ingest.py` | tiers, auto-selector, corruption metric, provenance writer, `lib ingest` runner |
| `lodlib/cli.py` | `cmd_ingest` + the `ingest` subparser (lazy-imports `ingest`) |
| `lodlib/normalize.py` | `strip_page_furniture` — reused by ingest to clean each extraction |
| `<lib>/PROVENANCE.jsonl` | the append-only provenance ledger (git-tracked) |
