#!/usr/bin/env python3
"""extract_bakeoff.py — rank PDF extraction backends against the rendered page, not against each other.

**Which ground truth, precisely.** The truth this harness ranks on is the *rendered page*: the PDF
rasterised and OCR-read (`visual.py`). It does NOT read `tests/fixtures/pdfs/GROUND-TRUTH.json` —
that file labels the small hand-built corpus by construction and is consumed by
`tests/test_pdf_corpus.py`, which checks the corpus itself. Saying "ground truth" without naming
which one invited the reading that this harness scores against those labels; it does not, and it
works on any PDF, labeled or not.


    python3 eval/extract_bakeoff.py --pdfs tests/fixtures/pdfs [--tiers pdftotext,docling]

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
REPORT_SCHEMA = "klode.extract-bakeoff/v2"
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


def _median(vals) -> float | None:
    """The TRUE median — the mean of the two middle values when the count is even. Reporting
    `sorted(v)[len(v)//2]` as "median" gave 1.0 for `[0.0, 1.0]`, i.e. the better of two backends'
    scores was presented as the pair's midpoint."""
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    mid = len(v) // 2
    return v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2


def _extract(pdf: Path, tier: str) -> tuple[str, str]:
    """(text, error). A missing backend is an error string, not an exception — the harness must
    report which backends it could not test rather than quietly testing fewer."""
    try:
        fn = pdfmod._EXTRACTORS[tier]
    except KeyError:
        return "", f"unknown tier {tier}"
    try:
        return fn(pdf, "eng"), ""
    except ImportError as e:
        return "", f"not installed ({e})"
    except (RuntimeError, OSError) as e:
        return "", f"failed ({e})"


def _structured_pages(pdf: Path, tier: str, cache: dict) -> "dict[int, str] | None":
    """Per-page text from a backend that carries structure instead of form feeds. None when the
    backend cannot say — never inferred from the markdown, which is the guess this whole harness
    exists to avoid.

    CACHED per (document, tier). Without it the remote backends were converted TWICE per document:
    once by `_extract` for the text, once here for the page text. That doubles the most expensive
    work in the harness — marker is minutes per document — and worse, the two calls are separate
    nondeterministic executions, so `words`/`containment` could describe a different conversion
    than `visual`. A metric and its explanation must come from the same run.
    """
    fn = {"docling": pdfmod.docling_page_text, "marker": pdfmod.marker_page_text}.get(tier)
    if fn is None:
        return None
    key = (_doc_id(pdf), tier)
    if key not in cache:
        try:
            cache[key] = fn(pdf)
        except (RuntimeError, OSError, ImportError):
            cache[key] = None             # unavailable is `visual=None` with a note, not a crash
    return cache[key]


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


def bake_off(pdfs: list[Path], tiers: list[str], *, sample: int = 3, seed: int = 1618,
             anchors: dict | None = None, out: Path | None = None, resume: bool = False) -> dict:
    if out is not None:
        # `--out` naming an input would have `_checkpoint` overwrite that PDF — in a multi-file run,
        # destroying a document before it is measured.
        clash = {_doc_id(p) for p in pdfs} & {_doc_id(out)}
        if clash:
            raise SystemExit(f"--out {out} is one of the input PDFs; refusing to overwrite it")
    manifest = _manifest(pdfs, tiers, sample, seed, anchors)
    report: dict = {"schema": REPORT_SCHEMA, "seed": seed, "sample_pages_per_pdf": sample,
                    "manifest": manifest, "pdfs": {}, "skipped": {}}
    done: set[str] = set()
    if resume:
        report = _load_checkpoint(out, manifest)          # exits non-zero rather than restarting
        done = set(report["pdfs"])
        print(f"resuming: {len(done)} document(s) already measured", file=sys.stderr)
    structured_cache: dict = {}
    for pdf in pdfs:
        if _doc_id(pdf) in done:
            continue
        declared, declared_err = _declared(pdf)
        control_raw, control_err = _extract(pdf, "pdftotext")
        per_tier: dict = {}
        for tier in tiers:
            text, err = _extract(pdf, tier)
            if err:
                report["skipped"].setdefault(tier, err)
                # Record the FAILED pair too. Dropping it made `pdfs` count only successes, so a
                # backend that failed on half the corpus was reported as "scored 1/1" — full
                # coverage — and outranked one measured on every document. That is a confident
                # verdict on unmeasured evidence, the exact class this repo exists to refuse.
                per_tier[tier] = {"visual": None, "visual_order": None, "extraction_error": err}
                continue
            row: dict = {"words": len(text.split())}

            # fidelity — the ranking signal
            pages = {i: t for i, t in enumerate(coverage.split_pages(text), start=1)} \
                if "\f" in text else {}
            if not pages:
                # A markdown-only backend has no form feeds, so docling — the backend this harness
                # exists to evaluate — scored `visual=None` on every document and could never be
                # ranked. Its STRUCTURED result does carry the boundary; ask for it.
                pages = _structured_pages(pdf, tier, structured_cache) or {}
            if not pages and control_raw:
                # a backend with no page separators cannot be page-matched; say so rather than
                # silently score it against the wrong text
                row["visual"] = None
                row["visual_note"] = "no page separators — cannot align pages for visual check"
            else:
                sel = visual.sample_pages(declared, sample, seed)
                vr = visual.check_pages(pdf, pages, pages=sel, seed=seed)
                # `vr.measured`, not `vr.ran`: rendering can run while every sampled page errors,
                # which produced `visual: None` with no note and threw the per-page errors away.
                row["visual"] = vr.quantile(0.5) if vr.measured else None
                # Recall is order-blind: a fully reversed page contains every OCR token and scores
                # 1.0. Ranking on recall alone therefore could not distinguish the scrambling this
                # harness exists to catch, even though `visual.py` had already measured it.
                row["visual_order"] = _median([c.order for c in vr.checks if c.order is not None])
                row["visual_worst"] = None if not vr.measured else min(vr.recalls)
                row["visual_worst_order"] = vr.worst_order
                row["visual_sampled"] = list(vr.sampled)
                row["visual_pages_measured"] = len(vr.recalls)
                if not vr.measured:
                    # an abstention ALWAYS carries its reason; a bare None reads as "fine"
                    row["visual_note"] = vr.skipped or "; ".join(
                        f"p{c.page}: {c.error}" for c in vr.checks if c.error) or "no page scored"

            # telemetry — reported, not ranked on
            if control_raw and tier != "pdftotext":
                # real page boundaries, not fixed windows: without them a book with short pages can
                # have every page internally reversed while each straddling window scores ~0.98
                agr = agreement.compare(control_raw, text,
                                        control_pages=coverage.split_pages(control_raw))
                row["containment"] = round(agr.containment, 4)
                row["inflation"] = round(agr.inflation, 4)
                # `Agreement.median` is the true median; `quantile(0.5)` is a nearest-rank pick and
                # calling that "order_median" was the same mislabel already fixed in integrity.py
                row["order_median"] = None if agr.median is None else round(agr.median, 4)
            row["declared_pages"] = declared
            if anchors and pdf.stem in anchors:
                row["anchor_compatibility"] = anchor_compatibility(text, anchors[pdf.stem])
            per_tier[tier] = row
        report["pdfs"][_doc_id(pdf)] = {"name": pdf.name, "declared_pages": declared,
                                        "declared_error": declared_err,
                                        "control_error": control_err, "tiers": per_tier}
        _checkpoint(report, out)             # after EVERY document, not at the end
    report.update(_aggregate(report, tiers))
    _checkpoint(report, out)                 # the final write carries the ranking too
    return report


def _aggregate(report: dict, tiers: list[str]) -> dict:
    """Rank by a PAIRED comparison, and refuse to rank when the pairing is empty.

    The defect this replaces, reproduced: a backend that scored 1.0 on one document and failed on
    the other outranked a backend that scored 0.9 on BOTH — and was printed as `scored 1/1`, which
    reads as complete coverage. Two independent auditors found it. Its cause was that failures were
    dropped from the report entirely, so the denominator counted only successes.

    Two changes make that unrepresentable:

    1. **Every requested (document, tier) pair is a row**, extraction failures included, so
       `attempted` is the corpus size and `measured/attempted` is visible.
    2. **The ranking runs over the documents EVERY ranked backend measured** — the paired set. A
       backend compared on a different subset is not being compared; that is the whole finding.
       Backends that measured nothing in the paired set are reported, unranked, with a reason.

    Per-tier medians over the *full* corpus are still reported as `median_visual_all`, clearly
    separated from the paired number the ranking uses, because they answer a different question
    ("how did it do on what it could do") and confusing the two is what caused this.
    """
    docs = report["pdfs"]
    agg: dict = {}
    for tier in tiers:
        rows = [d["tiers"].get(tier) for d in docs.values()]
        scored = {k: d["tiers"][tier]["visual"] for k, d in docs.items()
                  if tier in d["tiers"] and d["tiers"][tier].get("visual") is not None}
        agg[tier] = {
            "attempted": len(docs),
            "measured": len(scored),
            "extraction_failures": sum(1 for r in rows if r and r.get("extraction_error")),
            "unscored": len(docs) - len(scored),
            "median_visual_all": _median(list(scored.values())),
            "_scored_by_doc": scored,
        }
    # The paired set: documents scored by every tier that is a CANDIDATE for ranking — not by every
    # tier that was requested. Intersecting over all requested tiers meant one unavailable backend
    # (`--tiers a,b,marker` with marker not installed) emptied the set and suppressed the perfectly
    # good a-vs-b comparison. An independent verification caught that: the fix for a fail-OPEN had
    # become a fail-CLOSED, which is a different way of returning the wrong answer.
    #
    # A candidate is a tier that measured at least one document. A tier that measured nothing is
    # reported as unrankable with its reason and does not constrain anyone else's pairing.
    # A backend with too little shared coverage is DROPPED from the ranking and named, rather than
    # shrinking everyone else's basis. Intersecting over all candidates meant one half-measured
    # backend reduced the paired set below the threshold and suppressed a comparison between two
    # backends that had each measured everything — the same over-correction as intersecting over
    # unavailable tiers, one step subtler.
    #
    # Greedy and explainable: keep dropping the least-covered candidate until the survivors share
    # enough documents. Each exclusion is reported with its coverage, so "why isn't X ranked" always
    # has an answer in the report.
    candidates = [t for t in tiers if agg[t]["_scored_by_doc"]]
    excluded: dict[str, str] = {}
    while candidates:
        paired = set(docs)
        for tier in candidates:
            paired &= set(agg[tier]["_scored_by_doc"])
        if len(paired) >= MIN_PAIRED_DOCUMENTS or len(candidates) < 2:
            break
        worst = min(candidates, key=lambda t: (len(agg[t]["_scored_by_doc"]), t))
        excluded[worst] = (f"measured {agg[worst]['measured']}/{agg[worst]['attempted']} documents "
                           "— too few shared with the other backends to compare fairly")
        candidates.remove(worst)
    else:
        paired = set()
    for tier in tiers:
        a = agg[tier]
        vals = [a["_scored_by_doc"][k] for k in paired if k in a["_scored_by_doc"]]
        orders = [docs[k]["tiers"][tier].get("visual_order") for k in paired
                  if docs[k]["tiers"].get(tier, {}).get("visual_order") is not None]
        a["median_visual"] = _median(vals)
        a["median_order"] = _median(orders)
        # Actively inverted reading order is not a lower score on the same axis — it is a different
        # failure, and recall cannot see it. A backend measured as inverted sorts below every
        # backend that is not, whatever its recall. Same line the integrity gate draws
        # (`Thresholds.min_median_order`).
        a["order_inverted"] = a["median_order"] is not None and a["median_order"] < 0.0
        a["rankable"] = a["median_visual"] is not None and tier in candidates
        a.pop("_scored_by_doc")
    # A single shared document cannot order two backends, and more than one tier must exist for
    # "ranking" to mean anything. Below that bar `ranking` is EMPTY — not an order plus a caveat in
    # a neighbouring field. A caller reading `report["ranking"]` gets the refusal itself, because a
    # warning that only appears in the printed output is a warning the programmatic consumer never
    # sees. Same rule as `Integrity.abstained`: the unknown answer must not be shaped like a result.
    enough = len(paired) >= MIN_PAIRED_DOCUMENTS and len(candidates) >= 2
    rankable = [t for t in tiers if agg[t]["rankable"]] if enough else []
    if enough:
        note = ""
        unrankable = {t: excluded.get(
            t, f"measured {agg[t]['measured']}/{agg[t]['attempted']} documents; none in the "
               f"{len(paired)}-document set the ranked backends share")
            for t in tiers if not agg[t]["rankable"]}
    else:
        note = (f"NOT RANKED — {len(candidates)} backend(s) measured anything, sharing "
                f"{len(paired)} document(s); a comparison needs at least "
                f"{MIN_PAIRED_DOCUMENTS} shared documents and 2 measuring backends.")
        unrankable = {t: excluded.get(t, note) for t in tiers}
    ranking = sorted(rankable, key=lambda t: (agg[t]["order_inverted"],
                                              -(agg[t]["median_visual"] or 0.0), t))
    return {"aggregate": agg, "ranking": ranking, "unrankable": unrankable,
            "paired_documents": sorted(paired), "ranking_note": note}


def main(argv=None) -> int:
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
    if args.resume and not args.out:
        raise SystemExit("--resume needs --out: there is nothing to resume from")
    # Every one of these produced an empty-but-exit-0 report, which reads as "measured, nothing to
    # report" instead of "never ran".
    if args.sample < 1:
        raise SystemExit(f"--sample must be >= 1, got {args.sample}")
    tiers = [t.strip() for t in args.tiers.split(",") if t.strip()]
    if not tiers:
        raise SystemExit("--tiers is empty")
    if len(set(tiers)) != len(tiers):
        raise SystemExit(f"--tiers repeats a backend: {args.tiers}")

    root = Path(args.pdfs).expanduser()
    if not root.exists():
        raise SystemExit(f"--pdfs {root} does not exist")
    pdfs = sorted(root.glob("*.pdf")) if root.is_dir() else [root]
    pdfs = [p for p in pdfs if p.is_file()]
    if not pdfs:
        raise SystemExit(f"no PDF files found in {root}")
    anchors = _load_anchors(Path(args.anchors)) if args.anchors else None

    rep = bake_off(pdfs, tiers, sample=args.sample, seed=args.seed, anchors=anchors,
                   out=Path(args.out).expanduser() if args.out else None, resume=args.resume)
    if args.json:
        print(json.dumps(rep, indent=2))
        return 0

    print(f"extraction bake-off — {len(pdfs)} PDF(s), seed {rep['seed']}\n")
    print(f"{'pdf':<22}{'tier':<12}{'visual':>8}{'v.order':>8}"
          f"{'contain':>9}{'inflate':>9}{'order':>8}{'compat':>8}")
    print("-" * 84)
    for doc in rep["pdfs"].values():
        for tier, row in doc["tiers"].items():
            fmt = lambda v: "  n/a" if v is None else f"{v:.3f}"       # noqa: E731
            print(f"{doc['name'][:21]:<22}{tier:<12}{fmt(row.get('visual')):>8}"
                  f"{fmt(row.get('visual_order')):>8}"
                  f"{fmt(row.get('containment')):>9}{fmt(row.get('inflation')):>9}"
                  f"{fmt(row.get('order_median')):>8}{fmt(row.get('anchor_compatibility')):>8}")
    paired = len(rep["paired_documents"])
    if rep["ranking"]:
        print(f"\nRANKING — paired over the {paired} document(s) EVERY backend measured "
              f"(of {len(rep['pdfs'])}).")
        print("A backend compared on a different subset is not being compared.")
        for i, tier in enumerate(rep["ranking"], 1):
            a = rep["aggregate"][tier]
            mv = "n/a" if a["median_visual"] is None else f"{a['median_visual']:.3f}"
            mo = "n/a" if a["median_order"] is None else f"{a['median_order']:.3f}"
            print(f"  {i}. {tier:<12} paired_visual={mv:<7} order={mo:<7} "
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
        mv = "n/a" if a["median_visual_all"] is None else f"{a['median_visual_all']:.3f}"
        print(f"  {tier:<12} measured {a['measured']}/{a['attempted']} docs  "
              f"median_visual_over_those={mv}"
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
