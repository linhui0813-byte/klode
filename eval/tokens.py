#!/usr/bin/env python3
"""Token / Level-of-Zoom efficiency — does "pull the cheapest layer that answers" actually pay?

    python3 eval/tokens.py [-c path/to/library.toml]

The LOD design's whole claim is that an agent answers "is this relevant?" from L1 (thin) and only
zooms to L2/L3 when it must. This measures the payoff: the token cost of each layer and how many ×
cheaper L0/L1/L2 are than reading the raw source (L3). Tokens are ESTIMATED at chars/4 (the usual
English rule of thumb; zero-dependency, so no real tokenizer) — treat as a ratio, not a exact count.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lode.lib.config import Config
from lode.lib import query as Q
from lode.lib.common import card_files, fm_get, front_matter, read


def est_tokens(s: str) -> int:
    return round(len(s) / 4)


def main(argv=None):
    ap = argparse.ArgumentParser(description="LOD token-efficiency report")
    ap.add_argument("-c", "--config", help="library.toml (default: nearest)")
    args = ap.parse_args(argv)
    cfg = Config.load(Path(args.config) if args.config else None)

    tot = {"L0 meta": 0, "L1 thin": 0, "L2 full": 0, "L3 source": 0}
    have = {"L1 thin": 0, "L2 full": 0, "L3 source": 0}
    n = 0
    l3_missing = 0
    for p in card_files(cfg):
        n += 1
        text = read(p)
        fm = front_matter(text)
        sec = Q.sections(text)
        tot["L0 meta"] += est_tokens(fm)
        thin = sec.get("thin", "")
        full = sec.get("full", "")
        # an "owed" placeholder body doesn't count as content
        if thin and "owed" not in thin.lower():
            tot["L1 thin"] += est_tokens(thin); have["L1 thin"] += 1
        if full and "owed" not in full.lower():
            tot["L2 full"] += est_tokens(full); have["L2 full"] += 1
        rel = fm_get(fm, "file")
        src = os.path.join(cfg.root, rel.strip()) if rel else None
        if src and os.path.exists(src):
            tot["L3 source"] += est_tokens(read(src)); have["L3 source"] += 1
        else:
            l3_missing += 1

    print(f"LOD token efficiency — {n} cards (est. tokens @ chars/4)\n")
    print(f"  {'level':<11} {'total tok':>12} {'avg/card':>10} {'cards w/ content':>17}")
    print("  " + "-" * 53)
    print(f"  {'L0 meta':<11} {tot['L0 meta']:>12,} {tot['L0 meta']//max(n,1):>10,} {n:>17}")
    for lvl in ("L1 thin", "L2 full", "L3 source"):
        h = have[lvl]
        avg = tot[lvl] // h if h else 0
        print(f"  {lvl:<11} {tot[lvl]:>12,} {avg:>10,} {h:>17}")
    if l3_missing:
        print(f"  (L3 for {l3_missing} card(s) not installed — git-ignored corpus — omitted)")

    # headline: answering from L1 vs reading the raw source it summarizes
    if have["L1 thin"] and have["L3 source"]:
        l1_avg = tot["L1 thin"] / have["L1 thin"]
        l3_avg = tot["L3 source"] / have["L3 source"]
        print(f"\n  answering from L1 instead of L3: ~{l3_avg/l1_avg:,.0f}× fewer tokens "
              f"(≈{l1_avg:,.0f} vs ≈{l3_avg:,.0f} per source)")
    if tot["L3 source"]:
        board_plus_thin = tot["L0 meta"] + tot["L1 thin"]
        print(f"  whole board (L0+L1, {n} cards) ≈ {board_plus_thin:,} tok vs the full corpus "
              f"L3 ≈ {tot['L3 source']:,} tok — {tot['L3 source']/max(board_plus_thin,1):,.0f}× smaller")
    return 0


if __name__ == "__main__":
    sys.exit(main())
