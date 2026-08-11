#!/usr/bin/env python3
"""extract_bakeoff.py — rank PDF extraction backends against ground truth, not against each other.

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

TIERS = ("pdftotext", "xberg", "docling")


def _declared(pdf: Path) -> int:
    try:
        out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, timeout=60)
    except (subprocess.SubprocessError, OSError):
        return 0
    for line in out.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split()[1])
    return 0


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
            if not pages and control_raw:
                # a backend with no page separators cannot be page-matched; say so rather than
                # silently score it against the wrong text
                row["visual"] = None
                row["visual_note"] = "no page separators — cannot align pages for visual check"
            else:
                sel = visual.sample_pages(declared, sample, seed)
                vr = visual.check_pages(pdf, pages, pages=sel, seed=seed)
                row["visual"] = None if not vr.ran else vr.quantile(0.5)
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
    print(f"{'pdf':<22}{'tier':<12}{'visual':>8}{'contain':>9}{'inflate':>9}{'order':>8}{'compat':>8}")
    print("-" * 76)
    for name, doc in rep["pdfs"].items():
        for tier, row in doc["tiers"].items():
            fmt = lambda v: "  n/a" if v is None else f"{v:.3f}"       # noqa: E731
            print(f"{name[:21]:<22}{tier:<12}{fmt(row.get('visual')):>8}"
                  f"{fmt(row.get('containment')):>9}{fmt(row.get('inflation')):>9}"
                  f"{fmt(row.get('order_median')):>8}{fmt(row.get('anchor_compatibility')):>8}")
    if rep["skipped"]:
        print("\nnot tested:")
        for tier, why in rep["skipped"].items():
            print(f"  {tier}: {why}")
    print("\nRanking is by `visual` (fidelity to the rendered page) — the only column not derived\n"
          "from another extractor. `compat` is anchor-resolution: a MIGRATION statistic, biased\n"
          "toward whichever backend authored the anchors and blind to reading order. Never rank on it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
