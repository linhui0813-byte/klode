"""The shared consult application layer.

Resolution, lens loading, and section projection live here ONCE, so the two frontends (CLI `consult`,
MCP `consult_dimension`) cannot drift in *what* they return — only in *how* they format it. This is
the structural fix for the CLI/MCP parity bug class (see the parity report §6): the adapters keep
their protocol semantics (exit codes, redirect wording, missing-section fallback, formatting), but the
policy that decides *content* is not duplicated.

Honest boundary: shared here = resolution + loading + section-selection. Adapter-owned = transport
status, redirect, exit codes, and presentation.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from . import query

# The headings an "engine" projection keeps alongside Craft (MCP audience=engine).
_ENGINE_KEYS = ("engine", "operational spec", "scorer")
_PROJECTIONS = ("writer", "engine", "full", "sections")


@dataclass(frozen=True)
class ConsultRequest:
    name: str
    projection: str = "writer"          # "writer" | "engine" | "full" | "sections"
    sections: tuple[str, ...] = ()       # requested section keywords (for projection="sections")


@dataclass(frozen=True)
class DimensionView:
    name: str
    status: str
    core_question: str
    cards: str
    gate: str | None
    body: str
    secs: dict[str, str]                 # lowercased-heading -> body (query.sections)


@dataclass(frozen=True)
class ConsultResult:
    outcome: str                         # "dimension"|"framework"|"source"|"ambiguous"|"none"
    view: DimensionView | None = None    # dimension only
    framework: dict | None = None        # framework only (query.framework dict)
    source_id: str | None = None         # source only
    selected: tuple[tuple[str, str], ...] = ()   # (heading, body) pairs — writer/engine/sections projection
    missing: tuple[str, ...] = ()        # requested section keywords not matched
    candidates: tuple = ()               # LensMatch[] for ambiguous/none
    note: str = ""                       # the shared ambiguous/none message


def _project(secs: dict, projection: str, sections: tuple[str, ...]) -> tuple[tuple, tuple]:
    """(selected, missing) — the section-selection both frontends used to duplicate.
    `full` returns nothing selected on purpose; the adapter renders the raw body for it."""
    if projection == "writer":
        return tuple((h, secs[h]) for h in secs if h == "craft"), ()
    if projection == "engine":
        keep = [h for h in secs if h == "craft" or any(k in h for k in _ENGINE_KEYS)]
        return tuple((h, secs[h]) for h in keep), ()
    if projection == "sections":
        sel: list = []
        missing: list = []
        for want in sections:
            hits = [(h, secs[h]) for h in secs if want.lower() in h]
            (sel.extend(hits) if hits else missing.append(want))
        return tuple(sel), tuple(missing)
    return (), ()                        # "full"


def _load_dimension(cfg: Config, name: str) -> DimensionView | None:
    d = query.dimension(cfg, name)
    if d is None:
        return None
    return DimensionView(name=d["name"], status=d["status"], core_question=d["core_question"],
                         cards=d["cards"], gate=query.gate_verdict(cfg, name),
                         body=d["body"], secs=query.sections(d["body"]))


def consult(cfg: Config, req: ConsultRequest) -> ConsultResult:
    """Resolve `req.name`, load the lens, and project its sections — the shared content decision.
    Frontends dispatch on `.outcome` and format; scoping (e.g. MCP dimension-only) is theirs."""
    if req.projection not in _PROJECTIONS:               # a typo must not silently behave like `full`
        raise ValueError(f"unknown projection {req.projection!r}; expected one of {_PROJECTIONS}")
    r = query.resolve(cfg, req.name)
    if r.outcome in ("ambiguous", "none"):
        return ConsultResult(outcome=r.outcome, candidates=r.candidates, note=r.note)
    m = r.match
    if m.kind == "dimension":
        view = _load_dimension(cfg, m.name)
        if view is None:                                        # resolved a name the loader can't load
            return ConsultResult(outcome="none", note=f"no lens for {req.name.strip()!r}.")
        sel, missing = _project(view.secs, req.projection, req.sections)
        return ConsultResult(outcome="dimension", view=view, selected=sel, missing=missing)
    if m.kind == "framework":
        fw = query.framework(cfg, m.name)
        if fw is None:
            return ConsultResult(outcome="none", note=f"no lens for {req.name.strip()!r}.")
        return ConsultResult(outcome="framework", framework=fw)
    return ConsultResult(outcome="source", source_id=m.name)
