#!/usr/bin/env python3
"""Retrieval efficacy harness — does `lib search` surface the RIGHT sources?

This is the one lodlib "efficiency" the runtime numbers can't answer: a search that returns in
19 ms but ranks the wrong card is useless. It scores the shipped BM25 ranker against a gold set
of real questions (`eval/retrieval.jsonl`: each `q` + the hand-labelled `relevant` card ids) and
A/Bs it against the pre-BM25 raw term-count ranking and the `--full` (L2-included) variant, on the
SAME questions — the measurement the research doc said to run before adopting FTS5/embeddings.

    python3 eval/retrieval.py [-c path/to/library.toml] [--gold eval/retrieval.jsonl] [-v]

Metrics (mean over questions): P@5, R@10, MRR, nDCG@10. NOTE: with an INCOMPLETE gold set the
scores are a LOWER BOUND (a truly-relevant card you didn't label counts as a miss), so grow the
gold set before trusting absolute numbers — the A/B *between* rankers is robust either way.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lode.lib.config import Config
from lode.lib import query as Q
from lode.lib.common import card_files, fm_get, front_matter, read

_STOP = {"the", "and", "for", "are", "was", "with", "that", "this", "from", "into", "versus",
         "vs", "when", "does", "how", "what", "why", "who", "some", "more", "than", "its"}


def terms_of(q: str) -> list[str]:
    """A user types keywords, not a sentence — derive content words the way a searcher would."""
    return [w for w in "".join(c.lower() if c.isalnum() else " " for c in q).split()
            if len(w) >= 3 and w not in _STOP]


def _blobs(cfg: Config, *, full: bool) -> list[tuple[str, str]]:
    out = []
    for p in card_files(cfg):
        text = read(p)
        fm = front_matter(text)
        sec = Q.sections(text)
        blob = " ".join([os.path.basename(p)[:-3], Q.title(text), fm_get(fm, "aliases") or "",
                         sec.get("thin", ""), sec.get("full", "") if full else ""]).lower()
        out.append((os.path.basename(p)[:-3], blob))
    return out


def rank_bm25(cfg, terms, *, full=False):
    return [h.id for h in Q.search(cfg, terms, full=full, limit=50)[0]]


def rank_rawcount(cfg, terms, *, full=False):
    """The pre-P4 ranking: raw summed substring frequency, no length normalization — kept here
    only to A/B against BM25 on identical features."""
    scored = []
    for cid, blob in _blobs(cfg, full=full):
        s = sum(blob.count(t) for t in terms)
        if s:
            scored.append((s, cid))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [cid for _, cid in scored[:50]]


# ---- metrics ----
def p_at_k(ranked, gold, k):
    return len(set(ranked[:k]) & gold) / k


def r_at_k(ranked, gold, k):
    return len(set(ranked[:k]) & gold) / len(gold) if gold else 0.0


def mrr(ranked, gold):
    for i, cid in enumerate(ranked, 1):
        if cid in gold:
            return 1.0 / i
    return 0.0


def ndcg_at_k(ranked, gold, k):
    dcg = sum(1.0 / math.log2(i + 2) for i, cid in enumerate(ranked[:k]) if cid in gold)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(k, len(gold))))
    return dcg / idcg if idcg else 0.0


def evaluate(cfg, gold_rows, ranker, *, full=False):
    agg = {"P@5": 0.0, "R@10": 0.0, "MRR": 0.0, "nDCG@10": 0.0}
    per = []
    for row in gold_rows:
        gold = set(row["relevant"])
        ranked = ranker(cfg, terms_of(row["q"]), full=full)
        m = {"P@5": p_at_k(ranked, gold, 5), "R@10": r_at_k(ranked, gold, 10),
             "MRR": mrr(ranked, gold), "nDCG@10": ndcg_at_k(ranked, gold, 10)}
        for k in agg:
            agg[k] += m[k]
        per.append((row["q"], m, ranked[:5], gold))
    n = len(gold_rows)
    return {k: v / n for k, v in agg.items()}, per


def main(argv=None):
    ap = argparse.ArgumentParser(description="retrieval efficacy: score search against a gold set")
    ap.add_argument("-c", "--config", help="library.toml (default: nearest)")
    ap.add_argument("--gold", default=str(Path(__file__).with_name("retrieval.jsonl")))
    ap.add_argument("-v", "--verbose", action="store_true", help="print per-question detail")
    args = ap.parse_args(argv)

    cfg = Config.load(Path(args.config) if args.config else None)
    gold_rows = [json.loads(l) for l in Path(args.gold).read_text().splitlines() if l.strip()]

    configs = [("raw-count", rank_rawcount, False),
               ("BM25 (L0/L1)", rank_bm25, False),
               ("BM25 +Full (L2)", rank_bm25, True)]
    print(f"retrieval efficacy — {len(gold_rows)} questions, {len(card_files(cfg))} cards\n")
    print(f"{'ranker':<18} {'P@5':>7} {'R@10':>7} {'MRR':>7} {'nDCG@10':>8}")
    print("-" * 50)
    detail = None
    for name, ranker, full in configs:
        means, per = evaluate(cfg, gold_rows, ranker, full=full)
        print(f"{name:<18} {means['P@5']:>7.3f} {means['R@10']:>7.3f} "
              f"{means['MRR']:>7.3f} {means['nDCG@10']:>8.3f}")
        if name == "BM25 (L0/L1)":
            detail = per
    if args.verbose and detail:
        print("\nper-question (BM25 L0/L1) — MRR · top-5 vs gold:")
        for q, m, top5, gold in detail:
            hit = "✓" if m["MRR"] else "✗"
            print(f"  {hit} MRR={m['MRR']:.2f} R@10={m['R@10']:.2f}  {q[:52]}")
            miss = gold - set(top5)
            if miss:
                print(f"      missed from top-5: {', '.join(sorted(miss))}")
    print("\nNote: incomplete gold ⇒ scores are a lower bound; the ranker A/B is what's robust.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
