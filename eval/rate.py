"""rate.py — does this rubric mean the same thing to two people?

This is the acceptance test for a **CriterionSpec**, not for a judge. klode's own architecture note
states the standard: *"would two domain experts independently reach the same verdict? If not, the
criterion is under-specified — fix the criterion, not the judge."* That makes inter-rater agreement
an instrument pointed at the rubric, and it is why authoring and labelling cannot be separate
phases: there is no other way to know a level descriptor is well defined than to have two people
apply it and measure where they diverge.

    mkdir -p .klode-ratings          # git-ignored: sheets carry draft text + evaluations

    # 1. emit a blind sheet per rater (drafts in a per-rater order, no scores visible)
    python3 eval/rate.py sheet -c LIB pacing --drafts drafts/ --rater alice > .klode-ratings/alice.jsonl
    python3 eval/rate.py sheet -c LIB pacing --drafts drafts/ --rater bob   > .klode-ratings/bob.jsonl

    # 2. each rater fills in "score" by matching a level descriptor. Then:
    python3 eval/rate.py score .klode-ratings/alice.jsonl .klode-ratings/bob.jsonl --bar 0.6

Reported per criterion, because the aggregate hides the thing you act on: ONE vague descriptor can
sink a rubric while every other criterion is fine, and only the per-criterion row tells you which
one to rewrite.

**Fail-closed by construction.** Every sheet carries a header pinning the rubric digest, the
draft set, and the rater; `score` refuses mismatched rubrics, the same rater twice, missing rows,
unfilled rows, and an undefined kappa. A rating run that cannot support a conclusion exits non-zero
rather than printing a clean one.

**What this does not measure.** Agreement is necessary, not sufficient: two raters can consistently
apply a criterion that measures the wrong thing. It also says nothing about the judge — that comes
after, against the rubric this certifies. Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from klode import lib          # noqa: E402
from klode.gate import load_spec, rubric_identity   # noqa: E402
from klode.gate.spec import canonical_digest as _canonical_digest   # noqa: E402

SHEET_SCHEMA = "klode.rating-sheet/v1"


def _drafts(d: Path) -> list[tuple[str, str]]:
    """(id, text) pairs. Identity is the relative path, not the stem: `x.md` and `x.txt` are two
    drafts, and collapsing them to `x` silently dropped one rater's row for the other's draft."""
    if d.is_file():
        return [(d.name, d.read_text(encoding="utf-8"))]
    out = []
    for p in sorted(d.rglob("*")):
        if p.is_file() and p.suffix in (".md", ".txt"):
            out.append((str(p.relative_to(d)), p.read_text(encoding="utf-8")))
    return out


def _rater_order(items, rater: str):
    """A deterministic per-rater permutation. Both raters see the same drafts in a pseudorandom
    order each, so a rater who drifts as they tire does not drift in lockstep with the other — that
    would look like agreement. Deterministic (hash of rater+id) so a sheet can be regenerated
    exactly. Not GUARANTEED to differ: with few drafts two raters can coincide."""
    return sorted(items, key=lambda it: hashlib.sha256(f"{rater}:{it[0]}".encode()).hexdigest())


def _digest(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()



def cmd_sheet(args) -> int:
    cfg = lib.Config.load(Path(args.config))
    spec = load_spec(cfg, args.dimension, require_stamp=not args.no_stamp)
    if not spec.approved:
        print(f"note: rubric is {spec.admission!r} — rate it anyway; that is how it earns approval",
              file=sys.stderr)
    drafts = _drafts(Path(args.drafts))
    if not drafts:
        raise SystemExit(f"no drafts found in {args.drafts}")
    ids = [d for d, _ in drafts]
    if len(set(ids)) != len(ids):
        raise SystemExit("duplicate draft ids — each draft must be identifiable on its own")

    # content, not just ids/scales: two revisions that reworded every descriptor but kept the ids
    # are DIFFERENT rubrics, and rating one cannot certify the other
    rubric_digest = rubric_identity(spec)
    draft_digest = _canonical_digest(
        {i: hashlib.sha256(t.encode()).hexdigest() for i, t in sorted(drafts)})
    print(json.dumps({"_sheet": {
        "schema": SHEET_SCHEMA, "rater": args.rater, "dimension": spec.dimension,
        "rubric_digest": rubric_digest, "draft_digest": draft_digest,
        "rows": len(drafts) * len(spec.criteria)}}, ensure_ascii=False))
    if args.inline:
        # once per draft, not once per criterion: repeating the body per row multiplied the sheet by
        # the criterion count and widened the disclosure surface for no benefit
        for did, text in sorted(drafts):
            print(json.dumps({"_draft": {"id": did, "text": text}}, ensure_ascii=False))
    for did, _ in _rater_order(drafts, args.rater):
        for c in spec.criteria:
            print(json.dumps({
                "rater": args.rater, "draft": did, "criterion": c.id,
                "statement": c.statement.value, "guidance": c.guidance.value,
                "levels": {str(l.score): l.descriptor.value for l in c.levels},
                "max_score": c.max_score,
                "score": None,          # <- the rater fills this by MATCHING a level descriptor
                "note": "",
            }, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- agreement


def _qwk(pairs: list[tuple[int, int]], k: int) -> float | None:
    """Quadratic-weighted Cohen's kappa: chance-corrected agreement for an ORDINAL scale, so being
    one level apart costs far less than being four apart. Raw percent-agreement would flatter a
    rubric whose raters both default to the middle band. Returns None when kappa is UNDEFINED
    (expected disagreement is zero — e.g. both raters constant), which the caller must treat as a
    failure to establish agreement, not as agreement."""
    if len(pairs) < 2 or k < 2:
        return None
    o = [[0] * k for _ in range(k)]
    for a, b in pairs:
        o[a][b] += 1
    n = len(pairs)
    ra = [sum(o[i]) for i in range(k)]
    rb = [sum(o[i][j] for i in range(k)) for j in range(k)]
    denom = (k - 1) ** 2
    num = den = 0.0
    for i in range(k):
        for j in range(k):
            w = (i - j) ** 2 / denom
            num += w * o[i][j]
            den += w * ra[i] * rb[j] / n
    if den == 0:
        return None
    return 1.0 - num / den


MAX_SCALE = 100          # a behavioral scale a human can apply; also bounds the k x k matrix


def _load(path: Path) -> tuple[dict, dict]:
    """(header, rows). Rows keep their filled/unfilled state — dropping unfilled rows at load time
    let a sheet that was blank in BOTH copies vanish from the comparison entirely and report clean
    agreement over whatever remained."""
    header, rows = None, {}
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError as e:
            raise SystemExit(f"{path}:{lineno}: invalid JSON — {e}")
        if "_sheet" in r:
            header = r["_sheet"]
            continue
        if "_draft" in r:
            continue
        key = (r.get("draft"), r.get("criterion"))
        if None in key:
            raise SystemExit(f"{path}:{lineno}: row is missing `draft` or `criterion`")
        if key in rows:
            raise SystemExit(f"{path}:{lineno}: duplicate row for {key} — last-write-wins would "
                             "silently pick one of two ratings")
        top = r.get("max_score")
        if isinstance(top, bool) or not isinstance(top, int) or not 2 <= top <= MAX_SCALE:
            raise SystemExit(f"{path}:{lineno}: `max_score` must be an integer in 2..{MAX_SCALE}, "
                             f"got {top!r}")
        score = r.get("score")
        if score is not None:
            if isinstance(score, bool) or not isinstance(score, int):
                raise SystemExit(f"{path}:{lineno}: `score` must be an integer naming a level, "
                                 f"got {score!r}")
            if not 0 <= score <= top:
                raise SystemExit(f"{path}:{lineno}: score {score} is outside this criterion's "
                                 f"scale 0..{top}")
        rows[key] = (score, top)
    if header is None:
        raise SystemExit(f"{path}: no `_sheet` header — regenerate it with `rate.py sheet`")
    declared = header.get("rows")
    if isinstance(declared, bool) or not isinstance(declared, int):
        # a missing / "3" / null / true count skipped the check entirely, which is the same hole
        raise SystemExit(f"{path}: header `rows` must be an integer, got {declared!r} — "
                         "regenerate the sheet with `rate.py sheet`")
    if declared != len(rows):
        # both sheets deleting the SAME row left the inventories equal and the comparison "complete"
        raise SystemExit(f"{path}: header declares {declared} rows but {len(rows)} were found — "
                         "rows were added or removed; regenerate the sheet")
    return header, rows


def _check_sheets(ha, hb, pa, pb) -> None:
    for h, p in ((ha, pa), (hb, pb)):
        if h.get("schema") != SHEET_SCHEMA:
            raise SystemExit(f"{p}: unsupported sheet schema {h.get('schema')!r}")
    if ha.get("rater") == hb.get("rater"):
        # comparing a sheet with itself certified one rater as inter-rater agreement
        raise SystemExit(f"both sheets are from rater {ha.get('rater')!r} — inter-rater agreement "
                         "needs two raters")
    for key, label in (("rubric_digest", "rubric"), ("draft_digest", "draft set"),
                       ("dimension", "dimension")):
        if ha.get(key) is None or hb.get(key) is None:
            # `None == None` let two header-less sheets compare as if they matched
            raise SystemExit(f"a sheet is missing its {label} identity — regenerate it with "
                             "`rate.py sheet`")
        if ha.get(key) != hb.get(key):
            raise SystemExit(f"the two sheets rate a different {label} "
                             f"({ha.get(key)!r} vs {hb.get(key)!r}) — they cannot be compared")


def cmd_score(args) -> int:
    if not math.isfinite(args.bar) or not 0.0 <= args.bar <= 1.0:
        raise SystemExit(f"--bar must be a finite number in 0.0..1.0, got {args.bar}")
    ha, a = _load(Path(args.a))
    hb, b = _load(Path(args.b))
    _check_sheets(ha, hb, args.a, args.b)

    if a.keys() != b.keys():
        only_a, only_b = sorted(a.keys() - b.keys()), sorted(b.keys() - a.keys())
        raise SystemExit(f"the sheets do not cover the same rows — {len(only_a)} only in "
                         f"{args.a}, {len(only_b)} only in {args.b}. A partial comparison cannot "
                         "certify a rubric.")
    unfilled = sorted(k for k in a if a[k][0] is None or b[k][0] is None)
    if unfilled:
        shown = ", ".join(f"{d}/{c}" for d, c in unfilled[:5])
        raise SystemExit(f"{len(unfilled)} row(s) unscored by at least one rater ({shown}"
                         f"{'…' if len(unfilled) > 5 else ''}) — finish the sheets first; a "
                         "partly-filled comparison would report agreement on a subset.")
    mismatched = [k for k in a if a[k][1] != b[k][1]]
    if mismatched:
        raise SystemExit(f"{len(mismatched)} row(s) declare different scales in the two sheets — "
                         "regenerate both from the same rubric")

    by_crit: dict[str, list] = {}
    for (draft, crit), (sa, ka) in a.items():
        by_crit.setdefault(crit, []).append(((sa, ka), b[(draft, crit)]))
    for crit, rows in by_crit.items():
        tops = {k for (_, k), _ in rows}
        if len(tops) != 1:
            # k is derived per criterion; rows disagreeing on the scale made a score index past the
            # matrix (IndexError) or silently compare incomparable bands
            raise SystemExit(f"criterion {crit!r} declares more than one scale ({sorted(tops)}) "
                             "across its rows — regenerate the sheets from one rubric")

    print(f"{'criterion':40} {'n':>4} {'exact':>7} {'±1':>7} {'QWK':>7}  verdict")
    print("-" * 84)
    failures = 0
    scales = set()
    for crit in sorted(by_crit):
        rows = by_crit[crit]
        k = rows[0][0][1] + 1
        scales.add(k)
        pairs = [(x, y) for (x, _), (y, _) in rows]
        exact = sum(1 for x, y in pairs if x == y) / len(pairs)
        adj = sum(1 for x, y in pairs if abs(x - y) <= 1) / len(pairs)
        qwk = _qwk(pairs, k)
        if qwk is None:
            # undefined is NOT agreement: with both raters constant there is no variation to agree
            # about, and the old code left `worst` untouched and printed a clean verdict anyway.
            verdict = "UNDEFINED — no variation to measure; rate more, more varied drafts"
            shown = "  n/a"
            failures += 1
        elif qwk < args.bar:
            verdict = "UNDER-SPECIFIED — rewrite its level descriptors"
            shown = f"{qwk:.3f}"
            failures += 1
        else:
            verdict, shown = "ok", f"{qwk:.3f}"
        print(f"{crit:40} {len(pairs):>4} {exact:>6.0%} {adj:>6.0%} {shown:>7}  {verdict}")

    allp = [(x, y) for rows in by_crit.values() for (x, _), (y, _) in rows]
    print("-" * 84)
    exact_all = sum(1 for x, y in allp if x == y) / len(allp)
    adj_all = sum(1 for x, y in allp if abs(x - y) <= 1) / len(allp)
    homogeneous = len(scales) == 1
    if homogeneous:
        overall = _qwk(allp, next(iter(scales)))      # `pop()` emptied the set that is tested below
        shown = "  n/a" if overall is None else f"{overall:.3f}"
    else:
        # 3 is the ceiling on a 0..3 criterion and 30% on a 0..10 one; pooling them as one ordinal
        # category makes the aggregate meaningless, so it is not reported
        shown = " mixed"
    print(f"{'OVERALL':40} {len(allp):>4} {exact_all:>6.0%} {adj_all:>6.0%} {shown:>7}"
          f"{'' if homogeneous else '   (scales differ — per-criterion rows are the result)'}")

    print(f"\nbar: QWK >= {args.bar} per criterion, over {len(a)} fully-rated rows.")
    if failures:
        print(f"VERDICT: not ready. {failures} criterion/criteria did not establish agreement;\n"
              "         fix the CRITERION (its level descriptors), not the raters.")
        return 1
    print("VERDICT: the rubric is applied consistently. It can be approved and labelled against.")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="rate.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sheet", help="emit a blind rating sheet for one rater")
    s.add_argument("-c", "--config", required=True)
    s.add_argument("dimension")
    s.add_argument("--drafts", required=True, help="a draft file or a directory of .md/.txt drafts")
    s.add_argument("--rater", required=True, help="rater id — also seeds this rater's draft order")
    s.add_argument("--inline", action="store_true", help="embed each draft's text in the sheet")
    s.add_argument("--no-stamp", action="store_true")
    s.set_defaults(fn=cmd_sheet)
    g = sub.add_parser("score", help="report inter-rater agreement between two filled sheets")
    g.add_argument("a")
    g.add_argument("b")
    g.add_argument("--bar", type=float, default=0.6,
                   help="minimum quadratic-weighted kappa per criterion (default 0.6)")
    g.set_defaults(fn=cmd_score)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
