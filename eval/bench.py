#!/usr/bin/env python3
"""Speed + scaling harness. Answers "how fast, and how does it grow?"

    python3 eval/bench.py [-c path/to/library.toml]

Three parts:
  1. real-corpus timings (median of N) for check / search / build, + corpus composition.
  2. a cProfile of `lib check` — where the time actually goes (expected: haystack normalization).
  3. a synthetic scaling sweep (100 → 5000 cards) confirming check and search stay ~linear.
"""
from __future__ import annotations

import argparse
import cProfile
import io
import pstats
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lode.lib.config import Config
from lode.lib import check as C, query as Q, build as B
from lode.lib.common import card_files, parse_markers, read, shelf_txts


def _median_ms(fn, n=7):
    ts = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t) * 1000)
    return statistics.median(ts), min(ts), max(ts)


def _synth_library(root: Path, n_cards: int) -> Path:
    """A throwaway library of `n_cards` small sources + built cards with one anchor each."""
    (root / "library.toml").write_text(
        '[library]\ndir = "library"\ncards = "cards"\nshelves = ["s"]\n'
        "[bibliography]\nenabled = false\n[copyright]\nguard = false\n", encoding="utf-8")
    lib = root / "library"
    (lib / "s").mkdir(parents=True)
    for i in range(n_cards):
        # ~1 KB of filler + a known phrase to anchor against
        (lib / "s" / f"src{i}.txt").write_text(
            f"filler word alpha beta gamma delta {i} " * 25 + f"\nthe unique phrase number {i} here.\n",
            encoding="utf-8")
    cfg = Config.load(root / "library.toml")
    B.build(cfg)
    for i in range(n_cards):
        p = lib / "cards" / f"src{i}.md"
        t = p.read_text(encoding="utf-8")
        head = t[: t.index("## Thin")]
        p.write_text(head + f'## Thin\nnovelty topic {i} — `grep: "unique phrase number {i}"`.\n'
                            "\n## Full\n_(owed)_\n", encoding="utf-8")
    return root / "library.toml"


def real_corpus(cfg: Config) -> None:
    cards, txts = card_files(cfg), shelf_txts(cfg)
    src_mb = sum(Path(t).stat().st_size for t in txts) / 1e6
    anchors = sum(len(parse_markers(read(p))) for p in cards)
    print(f"corpus: {len(cards)} cards · {len(txts)} sources · {src_mb:.1f} MB source · {anchors} anchors\n")

    med, lo, hi = _median_ms(lambda: C.check(cfg))
    print(f"  lib check   {med:8.1f} ms  (min {lo:.0f} / max {hi:.0f})  "
          f"= {med/max(len(cards),1):.2f} ms/card, {med/max(anchors,1):.2f} ms/anchor")
    med, *_ = _median_ms(lambda: Q.search(cfg, ["narrative", "emotion"]))
    print(f"  lib search  {med:8.2f} ms")
    med, *_ = _median_ms(lambda: B.build(cfg), n=3)
    print(f"  lib build   {med:8.1f} ms")

    print("\ncProfile of `lib check` (top cumulative frames):")
    pr = cProfile.Profile()
    pr.enable(); C.check(cfg); pr.disable()
    s = io.StringIO()
    pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(8)
    for line in s.getvalue().splitlines():
        if "lodlib" in line or "{method" in line or "function calls" in line:
            print("   " + line.strip())


def scaling() -> None:
    print("\nscaling sweep (synthetic corpora):")
    print(f"  {'cards':>6} {'check ms':>10} {'ms/card':>9} {'search ms':>10}")
    print("  " + "-" * 39)
    for n in (100, 500, 2000, 5000):
        tmp = Path(tempfile.mkdtemp(prefix=f"lodlib-scale-{n}-"))
        try:
            cfg = Config.load(_synth_library(tmp, n))
            cm, *_ = _median_ms(lambda: C.check(cfg), n=3)
            sm, *_ = _median_ms(lambda: Q.search(cfg, ["novelty", "topic"]), n=5)
            print(f"  {n:>6} {cm:>10.1f} {cm/n:>9.3f} {sm:>10.2f}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="lodlib speed + scaling benchmark")
    ap.add_argument("-c", "--config", help="library.toml (default: nearest)")
    ap.add_argument("--no-scaling", action="store_true", help="skip the synthetic sweep")
    args = ap.parse_args(argv)
    real_corpus(Config.load(Path(args.config) if args.config else None))
    if not args.no_scaling:
        scaling()
    return 0


if __name__ == "__main__":
    sys.exit(main())
