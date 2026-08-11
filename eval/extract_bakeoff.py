#!/usr/bin/env python3
"""extract_bakeoff.py — rank PDF extraction backends against the rendered page, not against each other.

**Which ground truth, precisely.** The truth this harness ranks on is the *rendered page*: the PDF
rasterised and OCR-read (`visual.py`). It does NOT read `tests/fixtures/pdfs/GROUND-TRUTH.json` —
that file labels the small hand-built corpus by construction and is consumed by
`tests/test_pdf_corpus.py`, which checks the corpus itself. Saying "ground truth" without naming
which one invited the reading that this harness scores against those labels; it does not, and it
works on any PDF, labeled or not.


    python3 eval/extract_bakeoff.py --pdfs tests/fixtures/pdfs [--tiers pdftotext,docling] [-v]

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
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from klode.lib import agreement, coverage, visual                          # noqa: E402
from klode.lib.formats import pdf as pdfmod                                # noqa: E402

TIERS = ("pdftotext", "xberg", "docling", "marker")


def _declared(pdf: Path) -> int:
    try:
        out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, timeout=60)
    except (subprocess.SubprocessError, OSError):
        return 0
    for line in out.stdout.splitlines():
        if line.startswith("Pages:"):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):     # `Pages: unknown` must not crash the harness
                return 0
    return 0


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


def _structured_pages(pdf: Path, tier: str) -> "dict[int, str] | None":
    """Per-page text from a backend that carries structure instead of form feeds. None when the
    backend cannot say — never inferred from the markdown, which is the guess this whole harness
    exists to avoid."""
    fn = {"docling": pdfmod.docling_page_text, "marker": pdfmod.marker_page_text}.get(tier)
    if fn is None:
        return None
    try:
        return fn(pdf)
    except (RuntimeError, OSError, ImportError):
        return None                       # unavailable is `visual=None` with a note, not a crash


def anchor_compatibility(text: str, anchors: list[str]) -> float | None:
    """Share of pre-existing anchors that still resolve. COMPATIBILITY ONLY — see the module
    docstring for why this must never rank backends."""
    if not anchors:
        return None
    from klode.lib.common import Marker, haystacks, resolve as resolve_marker
    hays = haystacks(text)
    return sum(1 for a in anchors if resolve_marker(Marker(a), hays).found) / len(anchors)


def bake_off(pdfs: list[Path], tiers: list[str], *, sample: int = 3, seed: int = 1618,
             anchors: dict | None = None) -> dict:
    report: dict = {"seed": seed, "sample_pages_per_pdf": sample, "pdfs": {}, "skipped": {}}
    for pdf in pdfs:
        declared = _declared(pdf)
        control_raw, control_err = _extract(pdf, "pdftotext")
        per_tier: dict = {}
        for tier in tiers:
            text, err = _extract(pdf, tier)
            if err:
                report["skipped"].setdefault(tier, err)
                continue
            row: dict = {"words": len(text.split())}

            # fidelity — the ranking signal
            pages = {i: t for i, t in enumerate(coverage.split_pages(text), start=1)} \
                if "\f" in text else {}
            if not pages:
                # A markdown-only backend has no form feeds, so docling — the backend this harness
                # exists to evaluate — scored `visual=None` on every document and could never be
                # ranked. Its STRUCTURED result does carry the boundary; ask for it.
                pages = _structured_pages(pdf, tier) or {}
            if not pages and control_raw:
                # a backend with no page separators cannot be page-matched; say so rather than
                # silently score it against the wrong text
                row["visual"] = None
                row["visual_note"] = "no page separators — cannot align pages for visual check"
            else:
                sel = visual.sample_pages(declared, sample, seed)
                vr = visual.check_pages(pdf, pages, pages=sel, seed=seed)
                row["visual"] = None if not vr.ran else vr.quantile(0.5)
                # Recall is order-blind: a fully reversed page contains every OCR token and scores
                # 1.0. Ranking on recall alone therefore could not distinguish the scrambling this
                # harness exists to catch, even though `visual.py` had already measured it.
                row["visual_order"] = None if not vr.ran else _median(
                    [c.order for c in vr.checks if c.order is not None])
                row["visual_sampled"] = list(vr.sampled)
                if not vr.ran:
                    row["visual_note"] = vr.skipped

            # telemetry — reported, not ranked on
            if control_raw and tier != "pdftotext":
                agr = agreement.compare(control_raw, text)
                row["containment"] = round(agr.containment, 4)
                row["inflation"] = round(agr.inflation, 4)
                row["order_median"] = (None if agr.quantile(0.5) is None
                                       else round(agr.quantile(0.5), 4))
            row["declared_pages"] = declared
            if anchors and pdf.stem in anchors:
                row["anchor_compatibility"] = anchor_compatibility(text, anchors[pdf.stem])
            per_tier[tier] = row
        report["pdfs"][pdf.name] = {"declared_pages": declared, "control_error": control_err,
                                    "tiers": per_tier}
    # Rank. Previously the harness printed tiers in insertion order and computed no aggregate —
    # deleting "the ranking logic" would have changed nothing, because there was none.
    agg: dict = {}
    for doc in report["pdfs"].values():
        for tier, row in doc["tiers"].items():
            a = agg.setdefault(tier, {"scored": [], "orders": [], "pdfs": 0, "unscored": 0})
            a["pdfs"] += 1
            if row.get("visual") is None:
                a["unscored"] += 1
            else:
                a["scored"].append(row["visual"])
            if row.get("visual_order") is not None:
                a["orders"].append(row["visual_order"])
    for tier, a in agg.items():
        a["median_visual"] = _median(a["scored"])
        a["median_order"] = _median(a["orders"])
        # Actively inverted reading order is not a lower score on the same axis — it is a different
        # failure, and recall cannot see it. A backend measured as inverted sorts below every
        # backend that is not, whatever its recall. Same line the integrity gate draws
        # (`Thresholds.min_median_order`).
        a["order_inverted"] = a["median_order"] is not None and a["median_order"] < 0.0
    # A backend with no measurable score cannot be ranked above one that was measured; it sorts
    # last and its reason is reported rather than being silently treated as zero.
    report["ranking"] = sorted(
        agg, key=lambda t: (agg[t]["median_visual"] is None, agg[t]["order_inverted"],
                            -(agg[t]["median_visual"] or 0.0), t))
    report["aggregate"] = agg
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdfs", required=True, help="a PDF or a directory of PDFs")
    ap.add_argument("--tiers", default=",".join(TIERS))
    ap.add_argument("--sample", type=int, default=3, help="pages per PDF for the visual check")
    ap.add_argument("--seed", type=int, default=1618)
    ap.add_argument("--anchors", help="JSON: {card_stem: [phrase, ...]} for the compatibility stat")
    ap.add_argument("--json", action="store_true", help="emit the raw report")
    args = ap.parse_args(argv)

    root = Path(args.pdfs).expanduser()
    pdfs = sorted(root.glob("*.pdf")) if root.is_dir() else [root]
    if not pdfs:
        raise SystemExit(f"no PDFs found in {root}")
    anchors = json.loads(Path(args.anchors).read_text()) if args.anchors else None

    rep = bake_off(pdfs, args.tiers.split(","), sample=args.sample, seed=args.seed, anchors=anchors)
    if args.json:
        print(json.dumps(rep, indent=2))
        return 0

    print(f"extraction bake-off — {len(pdfs)} PDF(s), seed {rep['seed']}\n")
    print(f"{'pdf':<22}{'tier':<12}{'visual':>8}{'v.order':>8}"
          f"{'contain':>9}{'inflate':>9}{'order':>8}{'compat':>8}")
    print("-" * 84)
    for name, doc in rep["pdfs"].items():
        for tier, row in doc["tiers"].items():
            fmt = lambda v: "  n/a" if v is None else f"{v:.3f}"       # noqa: E731
            print(f"{name[:21]:<22}{tier:<12}{fmt(row.get('visual')):>8}"
                  f"{fmt(row.get('visual_order')):>8}"
                  f"{fmt(row.get('containment')):>9}{fmt(row.get('inflation')):>9}"
                  f"{fmt(row.get('order_median')):>8}{fmt(row.get('anchor_compatibility')):>8}")
    print("\nRANKING (by median visual fidelity; inverted reading order demoted; "
          "unmeasurable backends last):")
    for i, tier in enumerate(rep["ranking"], 1):
        a = rep["aggregate"][tier]
        mv = "n/a" if a["median_visual"] is None else f"{a['median_visual']:.3f}"
        mo = "n/a" if a["median_order"] is None else f"{a['median_order']:.3f}"
        print(f"  {i}. {tier:<12} median_visual={mv:<7} order={mo:<7} "
              f"scored {len(a['scored'])}/{a['pdfs']} pdfs"
              + (f"  ({a['unscored']} unscorable)" if a["unscored"] else "")
              + ("  [READING ORDER INVERTED]" if a["order_inverted"] else ""))
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
