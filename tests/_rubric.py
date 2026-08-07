"""Test scaffolding: write a valid CriterionSpec for a synthetic KB.

The gate's sole input is an authored rubric, so every `review_draft` test needs one. This builds a
minimal VALID spec from the same `(move, [phrases])` shape the Craft-layer helpers already use, so
migrating a test to the rubric path costs one call rather than 60 lines of hand-written JSON.

It is deliberately NOT in the shipped package: `authoring.derive` refuses to invent warrants and
level descriptors, which is the product behaviour. Tests need canned ones, and canned text belongs
in the tests.
"""
from __future__ import annotations

import json
from pathlib import Path

from klode.gate import authoring


def spec_doc(cfg, dimension, panel, moves, *, levels: int = 11,
             admission: str = "human_approved", card: str | None = None) -> dict:
    """`moves` = [(name, [phrase, ...]), ...] — one criterion per move, cited to `card`.

    Defaults to 11 levels (0..10) so a test's absolute expectations read the same as they did under
    the old fixed 0-10 scale; pass `levels=6` to exercise a 0..5 rubric.
    """
    card = card or panel[0]
    criteria = []
    for name, phrases in moves:
        criteria.append({
            "id": f"{dimension}.{authoring.slug(name)}",
            "criticality": "required",
            "statement": {
                "value": name,
                "kind": "paraphrase",
                "warrant": f"test fixture: {name} restates the cited phrase",
                "evidence": [{"card": card, "phrase": p} for p in phrases],
            },
            "evidence": [{"card": card, "phrase": p} for p in phrases],
            "levels": [
                {"score": i, "descriptor": {"value": f"level {i} of {levels - 1}",
                                            "kind": "operator_policy",
                                            "warrant": "test fixture descriptor"}}
                for i in range(levels)
            ],
        })
    doc = authoring.build(cfg, dimension, panel, criteria, admission=admission)
    return doc


def write(cfg, dimension, panel, moves, **kw) -> Path:
    """Write the spec into the KB's `[frameworks].criteria` dir and return its path."""
    doc = spec_doc(cfg, dimension, panel, moves, **kw)
    out = Path(cfg.criteria) / f"{dimension}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return out
