"""Demo — review a deliberately inert, over-specified draft against the fixture KB's `pacing`
rubric (a human-approved CriterionSpec with behaviorally anchored 0..5 levels). Every cited defect
is verified against a real source by lib.verify. The KB is the committed synthetic fixture under
tests/fixtures/, resolved relative to this file, so the demo runs on any checkout.

    python3 examples/gate_demo.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))   # a real deploy would `pip install klode`

from klode import lib                                        # noqa: E402
from klode.gate import FixtureJudge, review_draft         # noqa: E402

KB = HERE.parent / "tests" / "fixtures" / "kb-fixture" / "library.toml"

DRAFT = (
    "The room was rectangular and measured exactly four metres by five metres. It held twelve "
    "chairs, each with four legs, arranged in three rows of four. The walls were painted off-white "
    "and the ceiling was two and a half metres high. Every object was ordinary; nothing in the "
    "room departed in any way from what one would expect a room to contain."
)  # inert, fully-specified exposition — nothing deviates from the default, nothing left to infer


def main() -> None:
    cfg = lib.Config.load(KB)
    # A judge that (as an LLM rubric judge would) marks the pacing moves LOW for this inert draft.
    judge = FixtureJudge({
        "pacing.cut-inferable": (1, "spends words on what the reader could infer — inert specification"),
        "pacing.vary-sentence-length": (1, "flat tempo; no variation of sentence length to steer the reader"),
        "pacing.end-on-a-turn": (2, "closes on a full resolution; nothing carries the reader forward"),
    })
    v = review_draft(cfg, DRAFT, "pacing", judge)
    print(f"VERDICT: {v.decision}   score {v.score}/100 (hurdle {v.hurdle})")
    print(f"grounded criteria: {len(v.lines)}   unavailable (blocked the verdict): {len(v.unavailable)}\n")
    print(f"rubric: {len(v.lines)} criteria, each scored on its own behavioral scale\n")
    print("Defects sent back with Recycle — each citation grounded in a real source by lib.verify:")
    for l in v.defects:
        g = l.grounding
        print(f"  • [{l.score}/{l.max_score} = {l.pct}%] {l.criterion.statement}")
        print(f"        {l.note}")
        print(f"        grounded: “{g.phrase[:58]}…”  →  {g.card}:{g.line}")


if __name__ == "__main__":
    main()
