"""Authoring a CriterionSpec: seed a candidate from the Craft layer, pin the corpus, approve.

The workflow this encodes is klode's governing rule made mechanical — *agents generate candidates;
only a human promotes them to canon*:

    derive  →  a CANDIDATE that deliberately does NOT validate (empty warrants, empty level
               descriptors). The validator's errors are the author's worklist.
    fill    →  a human writes the warrants and the behavioral level descriptors, and marks each
               field explicit / paraphrase / derived / operator_policy / unknown.
    approve →  `admission: human_approved`. Only then will the gate score against it.

`derive` never invents a warrant or a descriptor. It cannot: a warrant is the reason a claim follows
from a source, and a machine that could write that convincingly is exactly the machine whose output
this artifact exists to keep out of canon.
"""
from __future__ import annotations

import re

from klode import lib

from . import criteria as _crit
from . import spec as _spec

_SLUG = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    return _SLUG.sub("-", text.lower()).strip("-") or "criterion"


def fingerprint(cfg, panel) -> dict:
    """Pin every panel source by its CURRENT digest. Computed, never authored — a hand-typed
    fingerprint pins nothing, and the whole point is to detect drift the author did not notice."""
    sources = {}
    for card in panel:
        digest = lib.source_digest(cfg, card)
        if digest is None:
            raise ValueError(f"panel card {card!r}: source not installed — cannot pin it")
        sources[card] = digest
    return {"sources": sources}


def _field(value, kind, warrant="", evidence=()):
    out = {"value": value, "kind": kind}
    if warrant:
        out["warrant"] = warrant
    if evidence:
        out["evidence"] = list(evidence)
    return out


def _evidence(markers, card):
    out = []
    for m in markers:
        e = {"card": card, "phrase": m.phrase}
        for k in ("before", "after", "nth"):
            if getattr(m, k, None) is not None:
                e[k] = getattr(m, k)
        out.append(e)
    return out


def derive(cfg, dimension: str, *, levels: int = 6) -> dict:
    """A candidate spec seeded from the dimension's Craft moves.

    Every move becomes a criterion with a STABLE slug id and its real anchors, **each cited to the
    card it actually resolves in**. Everything requiring judgment is left blank on purpose: the
    statement is marked `paraphrase` with no warrant (a Craft move restates the source — it is not
    the source), and each level descriptor is an empty `operator_policy`. The result does not
    validate until a human fills it, which is the design.
    """
    if isinstance(levels, bool) or not isinstance(levels, int) or levels < 2:
        raise ValueError(f"levels must be an integer >= 2 (a scale needs two bands), got {levels!r}")
    crit, panel = _crit.load_criteria(cfg, dimension)

    out_criteria, seen = [], set()
    for c in crit:
        g = _crit.ground(cfg, c, panel, require_stamp=False)
        if not g.grounded:
            # Falling back to panel[0] INVENTED a citation: the anchor would be filed against a card
            # it does not resolve in, and the author would have no signal that it was made up.
            raise ValueError(
                f"{dimension!r} Craft move {c.statement!r} does not ground ({g.resolution}) — fix the "
                "synthesis's anchor before deriving a rubric from it")
        # per-anchor cards, not one card for the whole move: in the pacing fixture `slows the tempo`
        # resolves in `structure` while the move's first anchor resolves in `brevity`.
        ev = [{"card": card, **{k: v for k, v in (("phrase", m.phrase), ("before", m.before),
                                                  ("after", m.after), ("nth", m.nth)) if v is not None}}
              for m, card, _ in g.anchors]
        cid = f"{dimension}.{slug(c.statement)}"
        if cid in seen:                       # two moves whose bold heads slug identically
            raise ValueError(f"derived id {cid!r} collides — two Craft moves reduce to the same slug; "
                             "rename one move, or author the rubric's ids by hand")
        seen.add(cid)
        out_criteria.append({
            "id": cid,
            "criticality": "required",
            "statement": _field(c.statement, "paraphrase", "", ev),
            "guidance": (_field(c.guidance, "derived", "") if c.guidance
                         else _field(None, "unknown")),
            "levels": [{"score": i, "descriptor": _field("", "operator_policy", "")}
                       for i in range(levels)],
            "evidence": ev,
        })
    return {
        "schema": "klode.criterion-spec/v1",
        "dimension": dimension,
        "panel": list(panel),
        "admission": "candidate",
        "fingerprint": fingerprint(cfg, panel),
        "derived_from": f"_syntheses/{dimension}.md",
        "criteria": out_criteria,
    }


def build(cfg, dimension: str, panel, criteria: list, *, admission: str = "candidate") -> dict:
    """Assemble a spec document from already-authored criteria, pinning the corpus as it stands.

    The public builder for authoring tools and tests: it guarantees the two parts that must never be
    written by hand — the corpus fingerprint and, when approving, the digest binding the approval to
    this exact body — while leaving every judgment to the caller."""
    doc = {
        "schema": "klode.criterion-spec/v1",
        "dimension": dimension,
        "panel": list(panel),
        "admission": admission,
        "fingerprint": fingerprint(cfg, panel),
        "criteria": criteria,
    }
    if admission == "human_approved":
        doc["approved_digest"] = _spec.content_digest(doc)
    return doc


def field(value, kind, warrant="", evidence=()):
    """Public envelope constructor — see `spec.Field` for what each kind must carry."""
    return _field(value, kind, warrant, evidence)
