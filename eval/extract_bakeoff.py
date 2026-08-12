#!/usr/bin/env python3
"""extract_bakeoff.py — rank PDF extraction backends against the rendered page, not against each other.

**Which ground truth, precisely.** The truth this harness ranks on is the *rendered page*: the PDF
rasterised and OCR-read (`visual.py`). It does NOT read `tests/fixtures/pdfs/GROUND-TRUTH.json` —
that file labels the small hand-built corpus by construction and is consumed by
`tests/test_pdf_corpus.py`, which checks the corpus itself. Saying "ground truth" without naming
which one invited the reading that this harness scores against those labels; it does not, and it
works on any PDF, labeled or not.


    python3 eval/extract_bakeoff.py --pdfs DIR [--tiers pdftotext,docling] [--out r.json]

This is what decides whether `marker` (or any new backend) earns a place in the tier ladder. It is
deliberately a measurement rather than a recommendation: the project already refused to adopt BM25
on intuition and built an eval set first, and the same rule governs an extraction backend.

**Ranking is by fidelity to the rendered page** (`visual.py`), because that is the only signal here
that is not downstream of another extractor. Agreement against a control cannot rank backends: the
control is `pdftotext`, so ranking by agreement would simply crown whichever backend most resembles
`pdftotext` — including on the documents where `pdftotext` is the thing that failed.

**Anchor-resolution rate is reported, never ranked on.** It is biased by construction: existing
`(grep: …)` anchors were authored against whichever extraction was installed at the time, so that
backend wins for reasons unrelated to fidelity. Worse, anchor resolution is *order-insensitive* — a
fully column-scrambled extraction resolves 100% of its anchors while being unreadable, which is the
exact failure this whole effort exists to catch. It is a **migration/compatibility** statistic:
"how much of my existing corpus keeps working", not "which backend is better".

Backends that are not installed are reported as skipped, never silently omitted.

**The limit of the instrument, stated plainly.** The rendered-page truth is itself an OCR reading,
so the two columns of `visual` are not equally trustworthy:

- `visual` (recall) — *what* is on the page. Robust: it is a multiset comparison, and OCR losing a
  word costs every backend the same.
- `visual_order` — *in what sequence*. This compares the OCR's token order against the candidate's,
  and **tesseract's own reading order is not guaranteed on a complex layout.** On a two-column page
  where tesseract itself interleaves the columns, a backend that read the columns correctly is
  scored down for disagreeing with a scrambled reference. The error is not symmetric or random: it
  penalises exactly the backends this harness exists to reward.

So `visual_order` is trustworthy as a *detector of gross inversion* (a reversed page scores ≈ −1,
which no reference confusion produces) and NOT as a fine-grained ranking between two backends that
both score high. Do not read 0.95 vs 0.98 as a result. A tie on recall with one backend near −1 on
order is a result; a small order gap between two positive scores is noise until a human has looked
at the pages.

Recording this because the alternative is a number that looks decisive and is not.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from klode.lib import agreement, coverage, visual                          # noqa: E402
from klode.lib.formats import pdf as pdfmod                                # noqa: E402

TIERS = ("pdftotext", "xberg", "docling", "marker")
CONTROL_TIER = "pdftotext"
REPORT_SCHEMA = "klode.extract-bakeoff/v2"
EXIT_NOT_RANKED = 2        # could-not-compare is not success; CI reads the exit code
MIN_PAIRED_DOCUMENTS = 2   # one shared document cannot order two backends; it is an anecdote


def _declared(pdf: Path) -> tuple[int, str]:
    """(page_count, error). Every failure used to collapse to a bare `0`, which then selected no
    pages, abstained every visual measurement, and still let an alphabetical "ranking" print. The
    reason travels with the number so an unmeasurable document is visible as one."""
    try:
        out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return 0, "pdfinfo not installed (poppler)"
    except subprocess.TimeoutExpired:
        return 0, "pdfinfo timed out after 60s"
    except (subprocess.SubprocessError, OSError) as e:
        return 0, f"pdfinfo failed ({e})"
    if out.returncode != 0:
        return 0, f"pdfinfo exited {out.returncode}: {out.stderr.strip()[:120]}"
    for line in out.stdout.splitlines():
        if line.startswith("Pages:"):
            try:
                return int(line.split()[1]), ""
            except (IndexError, ValueError):
                return 0, f"pdfinfo reported an unparseable page count: {line.strip()[:60]}"
    return 0, "pdfinfo reported no page count"


def _doc_seed(seed: int, pdf: Path) -> int:
    """A deterministic per-document seed derived from the global one. Reproducible from
    (seed, document) alone, which is what makes the sample re-runnable, without giving every
    document the same page positions."""
    h = hashlib.sha256(f"{seed}:{_doc_id(pdf)}".encode()).digest()
    return int.from_bytes(h[:8], "big")


def _median(vals) -> float | None:
    """The TRUE median — the mean of the two middle values when the count is even. Reporting
    `sorted(v)[len(v)//2]` as "median" gave 1.0 for `[0.0, 1.0]`, i.e. the better of two backends'
    scores was presented as the pair's midpoint."""
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    mid = len(v) // 2
    return v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2


def _extract(pdf: Path, tier: str, cache: dict | None = None) -> tuple[str, str]:
    """(text, error), from ONE backend invocation whose page text is cached alongside.

    A missing backend is an error string, not an exception — the harness reports which backends it
    could not test rather than quietly testing fewer.
    """
    key = (_doc_id(pdf), tier)
    if cache is not None and key in cache:
        return cache[key][0], cache[key][3]
    text, pages, page_text, err = pdfmod.structured_extract(pdf, tier)
    if cache is not None:
        cache[key] = (text, pages, page_text, err)
    return text, err


def _structured_pages(pdf: Path, tier: str, cache: dict) -> "tuple[dict[int, str] | None, str]":
    """`(page_text, why_not)` from the SAME invocation that produced the text.

    The previous version ran the backend a second time, so the cache never held the first result
    and the two conversions could differ. It also discarded the reason on failure, after which the
    row blamed "no page separators" for what was actually an endpoint error.
    """
    key = (_doc_id(pdf), tier)
    if key not in cache:
        return None, "not extracted"
    _text, _pages, page_text, err = cache[key]
    if err:
        return None, err
    return page_text, "" if page_text else "backend supplied no page provenance"


def _load_anchors(path: Path) -> dict:
    """`{pdf_stem: [phrase, ...]}`, validated. An unvalidated string value was iterated
    CHARACTER BY CHARACTER as if each letter were an anchor, producing a confident compatibility
    number from nonsense."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"--anchors {path}: {e}")
    if not isinstance(raw, dict):
        raise SystemExit(f"--anchors {path}: expected an object of pdf_stem -> [phrase, ...]")
    for stem, phrases in raw.items():
        if not isinstance(phrases, list) or not all(isinstance(x, str) and x.strip()
                                                    for x in phrases):
            raise SystemExit(f"--anchors {path}: [{stem!r}] must be a list of non-empty strings")
    return raw


def anchor_compatibility(text: str, anchors: list[str]) -> float | None:
    """Share of pre-existing anchors that still resolve. COMPATIBILITY ONLY — see the module
    docstring for why this must never rank backends."""
    if not anchors:
        return None
    from klode.lib.common import Marker, haystacks, resolve as resolve_marker
    hays = haystacks(text)
    return sum(1 for a in anchors if resolve_marker(Marker(a), hays).found) / len(anchors)


def _checkpoint(report: dict, out: Path | None) -> None:
    """Persist the partial report after every document, atomically.

    A full run over a real corpus is hours: two model backends convert every page, and on a host
    where the VLM inference server cannot start, marker stalls ~10 minutes per affected document
    before falling back. Holding every result in memory and printing once at the end meant a crash
    on the last document threw away the whole run — and a run that expensive will eventually meet
    an OOM, a dropped endpoint, or a closed laptop lid. Temp-plus-rename so a crash mid-write
    cannot corrupt the checkpoint it is there to protect.
    """
    if out is None:
        return
    # mkstemp, not a predictable `<out>.part`: the fixed name follows a pre-planted symlink and is
    # shared by concurrent runs, which then clobber each other's temp file. Same defect class
    # already fixed in klode/lib/ingest.py and klode/gate/__main__.py.
    fd, name = tempfile.mkstemp(dir=out.parent, prefix=f".{out.name}.", suffix=".part")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, out)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _manifest(pdfs: list[Path], tiers: list[str], sample: int, seed: int, anchors) -> dict:
    """What must match for a checkpoint to be resumable.

    Seed and sample size alone are not the experiment. Adding a tier, editing a PDF, or changing
    the anchor set and then resuming merged old measurements with new ones and returned a ranking
    for a tier that was never run. Documents are identified by CONTENT, because a path can be
    replaced under the same name — and keyed by resolved path, because two directories can each
    hold a `report.pdf` and basenames silently collapsed them into one row.
    """
    docs = {}
    for p in pdfs:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        docs[_doc_id(p)] = h.hexdigest()
    blob = json.dumps(anchors, sort_keys=True) if anchors else ""
    return {"tiers": list(tiers), "sample": sample, "seed": seed, "documents": docs,
            "anchors_sha256": hashlib.sha256(blob.encode()).hexdigest(),
            "schema": REPORT_SCHEMA}


def _doc_id(p: Path) -> str:
    """A stable per-document key. `pdf.name` collapsed `a/report.pdf` and `b/report.pdf` into one
    report row, and on resume skipped the second as already done."""
    try:
        return p.resolve().as_posix()
    except OSError:
        return str(p)


def _load_checkpoint(out: Path | None, manifest: dict) -> dict:
    """The prior report, or a hard stop. Never a silent fresh start.

    `--resume` on a missing, unreadable, or incompatible checkpoint used to begin a new experiment
    and then OVERWRITE the damaged evidence with it — hours of measurement destroyed by the flag
    meant to preserve them. Resuming is now all-or-nothing: it either continues a compatible
    experiment or exits non-zero and leaves the file alone.
    """
    if out is None:
        raise SystemExit("--resume needs --out: there is nothing to resume from")
    if not out.is_file():
        raise SystemExit(f"--resume: no checkpoint at {out}. Run without --resume to start one.")
    try:
        prior = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"--resume: {out} is unreadable ({e}). It is NOT being overwritten — "
                         "move it aside and rerun without --resume if you mean to discard it.")
    if not isinstance(prior, dict) or not isinstance(prior.get("pdfs"), dict):
        raise SystemExit(f"--resume: {out} is not a bake-off report. Refusing to overwrite it.")
    old = prior.get("manifest")
    if old != manifest:
        diff = [k for k in set(manifest) | set(old or {}) if (old or {}).get(k) != manifest.get(k)]
        raise SystemExit(f"--resume: {out} describes a different experiment (differs in: "
                         f"{', '.join(sorted(diff)) or 'unknown'}). Merging them would report a "
                         "ranking for measurements that were never taken together.")
    return prior


def _measure_document(pdf: Path, tiers: list[str], *, sample: int, seed: int,
                      anchors: dict | None, cache: dict, skipped: dict) -> dict:
    """Every requested tier against one document. Extracted from `bake_off`, which had grown to 96
    lines mixing resume bookkeeping, per-document measurement, checkpointing and aggregation."""
    declared, declared_err = _declared(pdf)
    control_raw, control_err = _extract(pdf, CONTROL_TIER, cache)
    per_tier: dict = {}
    for tier in tiers:
        # the control IS pdftotext; running it again as a candidate paid for the same subprocess
        # twice per document
        text, err = ((control_raw, control_err) if tier == CONTROL_TIER
                     else _extract(pdf, tier, cache))
        if err:
            skipped.setdefault(tier, err)
            # Record the FAILED pair too. Dropping it made `pdfs` count only successes, so a
            # backend that failed on half the corpus was reported as "scored 1/1" — full
            # coverage — and outranked one measured on every document.
            per_tier[tier] = {"visual": None, "visual_order": None, "extraction_error": err}
            continue
        per_tier[tier] = _measure_tier(pdf, tier, text, control_raw, declared,
                                       sample=sample, seed=seed, anchors=anchors, cache=cache)
    return {"name": pdf.name, "declared_pages": declared, "declared_error": declared_err,
            "control_error": control_err, "tiers": per_tier}


def _measure_tier(pdf: Path, tier: str, text: str, control_raw: str, declared: int, *,
                  sample: int, seed: int, anchors: dict | None, cache: dict) -> dict:
    row: dict = {"words": len(text.split())}

    # fidelity — the ranking signal
    pages = {i: t for i, t in enumerate(coverage.split_pages(text), start=1)} if "\f" in text else {}
    why_no_pages = ""
    if not pages:
        # A markdown-only backend has no form feeds, so docling — the backend this harness exists
        # to evaluate — scored `visual=None` on every document and could never be ranked. Its
        # STRUCTURED result does carry the boundary, from the same invocation.
        structured, why_no_pages = _structured_pages(pdf, tier, cache)
        pages = structured or {}
    if not pages and control_raw:
        row["visual"] = None
        # the ACTUAL reason, not a guess: "no page separators" was reported even when the real
        # cause was an endpoint failure
        row["visual_note"] = why_no_pages or "no page separators — cannot align pages"
    else:
        # per-DOCUMENT seed: one global seed gave every equal-length document identical page
        # positions, so a shared structure was systematically included or excluded corpus-wide
        doc_seed = _doc_seed(seed, pdf)
        sel = visual.sample_pages(declared, sample, doc_seed)
        vr = visual.check_pages(pdf, pages, pages=sel, seed=doc_seed)
        # `vr.measured`, not `vr.ran`: rendering can run while every sampled page errors, which
        # produced `visual: None` with no note and threw the per-page errors away.
        row["visual"] = vr.quantile(0.5) if vr.measured else None
        # Recall is order-blind: a fully reversed page contains every OCR token and scores 1.0.
        row["visual_order"] = _median([c.order for c in vr.checks if c.order is not None])
        row["visual_worst"] = min(vr.recalls) if vr.measured else None
        row["visual_worst_order"] = vr.worst_order
        row["visual_sampled"] = list(vr.sampled)
        row["visual_pages_measured"] = len(vr.recalls)
        if not vr.measured:
            row["visual_note"] = vr.skipped or "; ".join(
                f"p{c.page}: {c.error}" for c in vr.checks if c.error) or "no page scored"

    # telemetry — reported, not ranked on
    if control_raw and tier != CONTROL_TIER:
        agr = agreement.compare(control_raw, text, control_pages=coverage.split_pages(control_raw))
        row["containment"] = round(agr.containment, 4)
        row["inflation"] = round(agr.inflation, 4)
        row["order_median"] = None if agr.median is None else round(agr.median, 4)
    row["declared_pages"] = declared
    if anchors and pdf.stem in anchors:
        row["anchor_compatibility"] = anchor_compatibility(text, anchors[pdf.stem])
    return row


def bake_off(pdfs: list[Path], tiers: list[str], *, sample: int = 3, seed: int = 1618,
             anchors: dict | None = None, out: Path | None = None, resume: bool = False) -> dict:
    if out is not None:
        # `--out` naming an input would have `_checkpoint` overwrite that PDF — in a multi-file
        # run, destroying a document before it is measured.
        if {_doc_id(p) for p in pdfs} & {_doc_id(out)}:
            raise SystemExit(f"--out {out} is one of the input PDFs; refusing to overwrite it")
    manifest = _manifest(pdfs, tiers, sample, seed, anchors)
    report: dict = {"schema": REPORT_SCHEMA, "seed": seed, "sample_pages_per_pdf": sample,
                    "manifest": manifest, "pdfs": {}, "skipped": {}}
    done: set[str] = set()
    if resume:
        report = _load_checkpoint(out, manifest)          # exits non-zero rather than restarting
        done = set(report["pdfs"])
        print(f"resuming: {len(done)} document(s) already measured", file=sys.stderr)
    cache: dict = {}
    for pdf in pdfs:
        if _doc_id(pdf) in done:
            continue
        report["pdfs"][_doc_id(pdf)] = _measure_document(
            pdf, tiers, sample=sample, seed=seed, anchors=anchors,
            cache=cache, skipped=report["skipped"])
        _checkpoint(report, out)             # after EVERY document, not at the end
    report.update(_aggregate(report, tiers))
    _checkpoint(report, out)                 # the final write carries the ranking too
    return report

def _tier_totals(docs: dict, tiers: list[str]) -> dict:
    """Per-tier coverage over the WHOLE corpus, before any pairing. Every requested (document,
    tier) pair is a row — including extraction failures — so `attempted` is the corpus size and
    `measured/attempted` is visible. Dropping failures is what let a backend measured on 4 of 20
    documents be reported as `scored 4/4`."""
    agg: dict = {}
    for tier in tiers:
        rows = [d["tiers"].get(tier) for d in docs.values()]
        scored = {k: d["tiers"][tier]["visual"] for k, d in docs.items()
                  if tier in d["tiers"] and d["tiers"][tier].get("visual") is not None}
        agg[tier] = {"attempted": len(docs), "measured": len(scored),
                     "extraction_failures": sum(1 for r in rows if r and r.get("extraction_error")),
                     "unscored": len(docs) - len(scored),
                     "median_visual_all": _median(list(scored.values())),
                     "_scored_by_doc": scored}
    return agg


def _best_comparable_subset(agg: dict, tiers: list[str]) -> tuple[list[str], set]:
    """The LARGEST set of backends sharing at least `MIN_PAIRED_DOCUMENTS` documents.

    Exhaustive, because there are at most a handful of tiers and the greedy version was WRONG.
    It dropped the backend with the fewest total measurements, which is not the same as the
    backend that blocks the intersection. Reproduced: A covers documents 1-3, B covers 1-4, C
    covers 4-7. A and B share three documents and are perfectly comparable — greedy dropped A
    (fewest), then B, and returned an empty ranking over C alone.

    Ties are broken by the larger shared-document set, then alphabetically, so the choice is
    deterministic and reproducible from the report alone.
    """
    from itertools import combinations
    candidates = [t for t in tiers if agg[t]["_scored_by_doc"]]
    best: tuple[list[str], set] = ([], set())
    for size in range(len(candidates), 1, -1):
        options = []
        for combo in combinations(candidates, size):
            shared = set.intersection(*(set(agg[t]["_scored_by_doc"]) for t in combo))
            if len(shared) >= MIN_PAIRED_DOCUMENTS:
                options.append((sorted(combo), shared))
        if options:
            # largest shared set wins; then alphabetical, for a stable answer
            options.sort(key=lambda o: (-len(o[1]), o[0]))
            best = options[0]
            break
    return best


def _paired_metrics(docs: dict, agg: dict, tiers: list[str], paired: set) -> None:
    """Fill in every tier's paired numbers, in place. A tier outside `paired` still gets its
    corpus-wide figures — they answer a different question and must not be confused with the
    comparison, which is what caused the original defect."""
    for tier in tiers:
        a = agg[tier]
        vals = [a["_scored_by_doc"][k] for k in paired if k in a["_scored_by_doc"]]
        orders = [docs[k]["tiers"][tier].get("visual_order") for k in paired
                  if docs[k]["tiers"].get(tier, {}).get("visual_order") is not None]
        a["median_visual"] = _median(vals)
        a["median_order"] = _median(orders)
        # A median over pages and then over documents lets a backend wreck almost half of both and
        # still score perfectly, so the tail survives aggregation.
        a["worst_visual"] = min(vals) if vals else None
        a["p10_visual"] = sorted(vals)[max(0, int(0.10 * (len(vals) - 1)))] if vals else None
        a["worst_order"] = min(orders) if orders else None
        a["scores"] = sorted(round(v, 4) for v in vals)
        # Actively inverted reading order is a different failure from a lower recall, and recall
        # cannot see it — same line `Thresholds.min_median_order` draws for the integrity gate.
        a["order_inverted"] = a["median_order"] is not None and a["median_order"] < 0.0


def _aggregate(report: dict, tiers: list[str]) -> dict:
    """Rank by a PAIRED comparison, and refuse to rank when no viable pairing exists.

    The defect this replaces, reproduced twice by independent auditors: a backend that scored 1.0
    on one document and failed on the other outranked a backend that scored 0.9 on BOTH, and was
    printed as `scored 1/1` — which reads as complete coverage.
    """
    docs = report["pdfs"]
    agg = _tier_totals(docs, tiers)
    ranked, paired = _best_comparable_subset(agg, tiers)
    _paired_metrics(docs, agg, tiers, paired)
    for tier in tiers:
        agg[tier]["rankable"] = tier in ranked
        agg[tier].pop("_scored_by_doc")

    if ranked:
        note = ""
        unrankable = {t: (f"measured {agg[t]['measured']}/{agg[t]['attempted']} documents; too few "
                          f"shared with the {len(ranked)} ranked backend(s) to compare fairly")
                      for t in tiers if t not in ranked}
    else:
        note = (f"NOT RANKED — no {MIN_PAIRED_DOCUMENTS}+ backends share at least "
                f"{MIN_PAIRED_DOCUMENTS} measured documents.")
        unrankable = {t: note for t in tiers}
    # Below the bar `ranking` is EMPTY, not an order plus a caveat in a neighbouring field: a
    # warning that only appears in printed output is one the programmatic caller never sees.
    ranking = sorted(ranked, key=lambda t: (agg[t]["order_inverted"],
                                            -(agg[t]["median_visual"] or 0.0), t))
    return {"aggregate": agg, "ranking": ranking, "unrankable": unrankable,
            "paired_documents": sorted(paired), "ranking_note": note}

def _parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdfs", required=True, help="a PDF or a directory of PDFs")
    ap.add_argument("--tiers", default=",".join(TIERS))
    ap.add_argument("--sample", type=int, default=3, help="pages per PDF for the visual check")
    ap.add_argument("--seed", type=int, default=1618)
    ap.add_argument("--anchors", help="JSON: {pdf_stem: [phrase, ...]} for the compatibility stat")
    ap.add_argument("--json", action="store_true", help="emit the raw report")
    ap.add_argument("--out", help="checkpoint the report here after EVERY pdf — a multi-hour run "
                                  "must survive a crash on its last document")
    ap.add_argument("--resume", action="store_true",
                    help="continue --out's experiment; exits non-zero if it is missing, corrupt, "
                         "or describes a different experiment (never silently restarts)")
    args = ap.parse_args(argv)
    # Every one of these produced an empty-but-exit-0 report, which reads as "measured, nothing to
    # report" instead of "never ran".
    if args.resume and not args.out:
        raise SystemExit("--resume needs --out: there is nothing to resume from")
    if args.sample < 1:
        raise SystemExit(f"--sample must be >= 1, got {args.sample}")
    tiers = [t.strip() for t in args.tiers.split(",") if t.strip()]
    if not tiers:
        raise SystemExit("--tiers is empty")
    if len(set(tiers)) != len(tiers):
        raise SystemExit(f"--tiers repeats a backend: {args.tiers}")
    unknown = [t for t in tiers if t not in pdfmod._EXTRACTORS]
    if unknown:
        raise SystemExit(f"unknown tier(s) {unknown}; choose from "
                         f"{sorted(pdfmod._EXTRACTORS)}")
    root = Path(args.pdfs).expanduser()
    if not root.exists():
        raise SystemExit(f"--pdfs {root} does not exist")
    pdfs = sorted(root.glob("*.pdf")) if root.is_dir() else [root]
    pdfs = [p for p in pdfs if p.is_file()]
    if not pdfs:
        raise SystemExit(f"no PDF files found in {root}")
    # a text file happily passed as a PDF and produced a confident empty report
    bad = [p for p in pdfs if p.read_bytes()[:5] != b"%PDF-"]
    if bad:
        raise SystemExit(f"not a PDF: {', '.join(p.name for p in bad[:3])}")
    return args, tiers, pdfs


def _render(rep: dict) -> None:
    print(f"extraction bake-off — {len(rep['pdfs'])} PDF(s), seed {rep['seed']}\n")
    print(f"{'pdf':<22}{'tier':<12}{'visual':>8}{'v.order':>8}"
          f"{'contain':>9}{'inflate':>9}{'order':>8}{'compat':>8}")
    print("-" * 84)
    fmt = lambda v: "  n/a" if v is None else f"{v:.3f}"       # noqa: E731
    for doc in rep["pdfs"].values():
        for tier, row in doc["tiers"].items():
            print(f"{doc['name'][:21]:<22}{tier:<12}{fmt(row.get('visual')):>8}"
                  f"{fmt(row.get('visual_order')):>8}"
                  f"{fmt(row.get('containment')):>9}{fmt(row.get('inflation')):>9}"
                  f"{fmt(row.get('order_median')):>8}{fmt(row.get('anchor_compatibility')):>8}")
    if rep["ranking"]:
        print(f"\nRANKING — paired over the {len(rep['paired_documents'])} document(s) EVERY "
              f"backend measured (of {len(rep['pdfs'])}).")
        print("A backend compared on a different subset is not being compared.")
        for i, tier in enumerate(rep["ranking"], 1):
            a = rep["aggregate"][tier]
            print(f"  {i}. {tier:<12} paired_visual={fmt(a['median_visual']):<7} "
                  f"worst={fmt(a['worst_visual']):<7} order={fmt(a['median_order']):<7} "
                  f"measured {a['measured']}/{a['attempted']} docs"
                  + (f"  ({a['extraction_failures']} extraction failures)"
                     if a["extraction_failures"] else "")
                  + ("  [READING ORDER INVERTED]" if a["order_inverted"] else ""))
        for tier, why in rep["unrankable"].items():
            print(f"  --  {tier:<12} NOT RANKED: {why}")
    else:
        print(f"\n{rep['ranking_note'] or 'NOT RANKED.'}")
    print("\nPER-BACKEND COVERAGE (what it managed, separate from what it scored):")
    for tier, a in rep["aggregate"].items():
        print(f"  {tier:<12} measured {a['measured']}/{a['attempted']} docs  "
              f"median_visual_over_those={fmt(a['median_visual_all'])}"
              + (f"  ({a['extraction_failures']} extraction failures)"
                 if a["extraction_failures"] else ""))
    if rep["skipped"]:
        print("\nnot tested:")
        for tier, why in rep["skipped"].items():
            print(f"  {tier}: {why}")
    print("\nRanking is by `visual` (fidelity to the rendered page) — the only column not derived\n"
          "from another extractor. `compat` is anchor-resolution: a MIGRATION statistic, biased\n"
          "toward whichever backend authored the anchors and blind to reading order. Never rank on it.\n"
          "`v.order` detects GROSS inversion (a reversed page scores about -1). It is not a fine\n"
          "ranking: the reference is tesseract's own reading order, which is itself unreliable on a\n"
          "complex layout, so a small gap between two positive scores is noise. Read the pages.")


def main(argv=None) -> int:
    args, tiers, pdfs = _parse_args(argv)
    anchors = _load_anchors(Path(args.anchors)) if args.anchors else None
    rep = bake_off(pdfs, tiers, sample=args.sample, seed=args.seed, anchors=anchors,
                   out=Path(args.out).expanduser() if args.out else None, resume=args.resume)
    # A run that ranked nothing exited 0 in both modes, so a caller could not tell "these backends
    # are ordered" from "nothing could be compared".
    rc = 0 if rep["ranking"] else EXIT_NOT_RANKED
    if args.json:
        print(json.dumps(rep, indent=2))
        return rc
    _render(rep)
    return rc

if __name__ == "__main__":
    raise SystemExit(main())
