"""Demo — review a deliberately info-dumpy worldbuilding draft against the worldbuilding dimension's
grounded Craft criteria. Every cited defect is verified against a real source by lib.

    python3 demo.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))   # a real deploy would `pip install lodlib`
sys.path.insert(0, str(HERE))

from lode import lib                                        # noqa: E402
from lode.gate import FixtureJudge, review_draft         # noqa: E402

DOXAI = Path("/Users/joker/github/xiaolai/myprojects/lode/corpus/kb-01-storycraft/library.toml")

DRAFT = (
    "The kingdom of Aldoria spans three continents. Its capital, Highspire, was founded 1,247 years "
    "ago by King Eldric the Third, whose bloodline traces to the ancient Sundering. The economy runs "
    "on tin, salt, and enchanted glass; the currency is the silver drake, subdivided into twelve "
    "copper wings. There are nine noble houses, each with a sigil, a motto, and a hereditary seat on "
    "the Grand Conclave, which convenes every equinox."
)  # an iceberg of exposition — nothing deviates from the reader's assumed default, nothing left to fill in


def main() -> None:
    cfg = lib.Config.load(DOXAI)
    # A judge that (as an LLM rubric judge would) marks the omission moves LOW for this info-dump.
    judge = FixtureJudge({
        "C1": (2, "pure status-quo exposition — spends words where nothing deviates from the default"),
        "C2": (3, "leaves no blanks; every atom specified, the reader given nothing to perform"),
    })
    v = review_draft(cfg, DRAFT, "worldbuilding", judge)
    print(f"VERDICT: {v.decision}   score {v.score}/100 (hurdle {v.hurdle})")
    print(f"grounded criteria: {len(v.lines)}   ungrounded (dropped, flagged): {len(v.ungrounded)}\n")
    print("Defects sent back with Recycle — each citation grounded in a real source by lib.verify:")
    for l in v.defects:
        g = l.grounding
        print(f"  • [{l.score}/10] {l.criterion.statement}")
        print(f"        {l.note}")
        print(f"        grounded: “{g.phrase[:58]}…”  →  {g.card}:{g.line}")


if __name__ == "__main__":
    main()
