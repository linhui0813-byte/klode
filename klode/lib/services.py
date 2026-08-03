"""klode/lib/services.py — the domain services + the shared executor.

Every verb's logic lives here ONCE, wrapping the KB-agnostic engine (`query`/`console`/`pool`) and
returning a core `OpResult` with structured provenance. `execute()` is the single entry point both
adapters call: it resolves scope (the kb-selection + fan-out policy), dispatches to the per-op
service, and stamps provenance — so the MCP server and the CLI cannot drift in behaviour, only in
formatting. Imports the engine + core + opspec; imports NO adapter.
"""
from __future__ import annotations

import hashlib

from . import common, console, core, opspec, query
from .config import ConfigError
from .core import CapabilityStatus, OpResult, Provenance, Scope

FANOUT_CAP = 10       # max KBs a discovery fan-out renders; the rest are announced, not dumped


def _bounded_int(params: dict, key: str, default: int, lo: int, hi: int) -> int:
    """Coerce an untrusted adapter param to an int within [lo, hi]. A bad type or an out-of-range
    value falls back to a safe bound rather than reaching the engine as a negative slice (which
    silently counts from the end) or an unbounded allocation."""
    try:
        n = int(params.get(key, default))
    except (TypeError, ValueError):
        return default
    return max(lo, min(n, hi))


class ScopeError(ConfigError):
    """A kb-scoped op could not resolve which KB to act on (none given and >1 registered, or none
    registered). A ConfigError subclass, so both adapters' existing handlers catch it."""


# ---------------------------------------------------------------------------
# scope resolution — the shared kb-selection + fan-out policy (one definition, both surfaces)
# ---------------------------------------------------------------------------
def resolve_scope(pool, spec: opspec.OpSpec, kb_arg: str | None) -> Scope:
    if spec.scope == "registry":
        return Scope.registry()
    ids = pool.ids()
    if kb_arg and kb_arg != "*":
        return Scope.kb(kb_arg)                       # explicit — pool.config validates it later
    if not ids:
        raise ScopeError("no KBs are registered")
    if pool.default:                                  # exactly one KB
        return Scope.kb(pool.default)
    if spec.fanout:                                   # discovery over many KBs
        return Scope.kbs(ids)
    raise ScopeError(f"no `kb` given and this server hosts multiple KBs — "
                     f"specify one of: {', '.join(ids)}")


# ---------------------------------------------------------------------------
# catalog services
# ---------------------------------------------------------------------------
def _svc_kbs_list(pool, params: dict):
    return pool.catalog()                             # list[registry.KBInfo]


def _svc_lenses_list(cfg, params: dict):
    dims, fws = query.lenses(cfg)
    return {"dimensions": list(dims), "frameworks": list(fws)}


def _svc_cards_list(cfg, params: dict):
    import os
    from .common import card_files
    out = []
    for p in card_files(cfg):
        cid = os.path.basename(p)[:-3]
        out.append({"id": cid, "title": query.card_title(cfg, cid)})
    return out


def _svc_search(cfg, params: dict):
    terms = params.get("terms") or []
    hits, total = query.search(cfg, terms, full=bool(params.get("full")),
                               limit=_bounded_int(params, "limit", 20, 1, 1000))
    return {"hits": list(hits), "total": total}


def _svc_diagnose(cfg, params: dict):
    return query.diagnose(cfg, params.get("symptom", ""))     # [(dim, core_q)] | [] | None


# ---------------------------------------------------------------------------
# content services (get, surfaced as consult + zoom)
# ---------------------------------------------------------------------------
def _svc_consult(cfg, params: dict):
    name = params.get("name", "")
    projection = params.get("projection", "writer")
    sections = tuple(params.get("sections", ()))
    res = console.consult(cfg, console.ConsultRequest(name, projection=projection, sections=sections))
    if res.outcome == "dimension":
        v = res.view
        craft = next((b for h, b in res.selected if h == "craft"), "")
        return core.DimensionResult(v.name, v.status, v.core_question, craft, v.cards, v.body,
                                    gate=v.gate, secs=dict(v.secs), selected=tuple(res.selected))
    if res.outcome == "framework":
        fw = res.framework
        return core.FrameworkResult(fw["name"], fw["dimension"], fw.get("title", ""),
                                    fw.get("aliases", ""), fw["sections"])
    if res.outcome == "source":
        cid = res.source_id
        return core.SourceCardResult(cid, query.card_title(cfg, cid),
                                     query.body(cfg, cid, "thin"), query.body(cfg, cid, "full"))
    return core.Note(res.outcome, res.note)                   # ambiguous | none


def _svc_zoom(cfg, params: dict):
    cid = params.get("id", "")
    level = params.get("level", "thin")
    if not query.card_path(cfg, cid):
        return None                                          # no such card — adapters render "No card"
    title = query.card_title(cfg, cid)
    if level == "meta":
        return core.CardContent(cid, level, title, query.meta(cfg, cid))
    if level in ("thin", "full"):
        return core.CardContent(cid, level, title, query.body(cfg, cid, level))
    src = query.source_of(cfg, cid)                           # level == content
    si = core.SourceInfo(rel=(src.rel if src else None),
                         installed=bool(src and src.installed),
                         size=(src.size if src else None))
    return core.CardContent(cid, level, title, None, source=si)


# ---------------------------------------------------------------------------
# grounding service — verify a phrase against a source. Proves OCCURRENCE, never entailment.
# Composed from existing primitives (common.resolve, source_sha256) — query.py is NOT modified.
# ---------------------------------------------------------------------------
def _stored_sha(cfg, card: str) -> str | None:
    p = query.card_path(cfg, card)
    if not p:
        return None
    val = common.fm_get(common.front_matter(common.read(p)), "source_sha256")
    return val.strip() if val and val.strip() else None


def _svc_verify(cfg, params: dict) -> core.EvidenceHit:
    card = params.get("card") or params.get("id", "")
    phrase = params.get("phrase", "")
    max_lines = _bounded_int(params, "max_lines", 10, 1, 1000)
    src = query.source_of(cfg, card)
    if src is None or not src.installed:
        return core.EvidenceHit(core.Resolution.SOURCE_NOT_INSTALLED, phrase,
                                card=card, rel=(src.rel if src else None))
    raw = src.path.read_bytes()
    cur_sha = hashlib.sha256(raw).hexdigest()                 # same convention as check.py freshness
    stored = _stored_sha(cfg, card)
    if stored and stored != cur_sha:                          # source drifted since the card was stamped
        return core.EvidenceHit(core.Resolution.SOURCE_STALE, phrase, card=card, rel=src.rel,
                                source_sha=cur_sha)
    hays = common.haystacks(raw.decode("utf-8", "replace"))
    res = common.resolve(common.Marker(phrase), hays)         # occurrence + ambiguity, via the shared matcher
    if not res.found:
        return core.EvidenceHit(core.Resolution.NOT_FOUND, phrase, card=card, rel=src.rel, source_sha=cur_sha)
    if res.ambiguous:
        return core.EvidenceHit(core.Resolution.AMBIGUOUS, phrase, card=card, rel=src.rel, source_sha=cur_sha)
    v = query.verify(cfg, card, phrase, max_lines=max_lines)
    resolution = core.Resolution.FOLDED_ONLY if (v and v.folded_only) else core.Resolution.FOUND
    return core.EvidenceHit(resolution, phrase, card=card, rel=src.rel,
                            lines=(tuple(v.lines) if v else ()), source_sha=cur_sha)


def _resolve_snapshot(cfg, card: str, anchor: "str | common.Marker", *, require_stamp: bool, today,
                      max_lines: int = 10) -> "tuple[core.EvidenceHit, list[str] | None]":
    """Read the source ONCE and resolve occurrence + freshness + review against that single snapshot,
    returning `(hit, source_lines)`. `source_lines` is the source split into lines (so a caller can
    window without re-reading) or None when the source is not installed. `anchor` is a bare phrase
    OR a full `common.Marker` — passing the Marker honours the linter's selector semantics (regex,
    prefix/suffix context, `#n` occurrence). Line location is case-SENSITIVE literal — matching the
    citation discipline (`check.py` treats anchors literally), which the occurrence-only `verify`
    op's case-insensitive lookup does not. This is the single read shared by `verify_evidence` and
    `verify_context`: no double read, no between-read TOCTOU."""
    from datetime import date

    marker = anchor if isinstance(anchor, common.Marker) else common.Marker(anchor)

    def hit(res, *, lines=(), sha=None, rel=None):
        return core.EvidenceHit(res, marker.phrase, card=card, rel=rel, lines=lines, source_sha=sha)

    src = query.source_of(cfg, card)
    if src is None or not src.installed:
        return hit(core.Resolution.SOURCE_NOT_INSTALLED, rel=(src.rel if src else None)), None
    raw = src.path.read_bytes()                                       # the ONE read
    cur_sha = hashlib.sha256(raw).hexdigest()                        # same convention as check.py freshness
    stored = _stored_sha(cfg, card)
    text = raw.decode("utf-8", "replace")
    source_lines = text.splitlines()
    if stored and stored != cur_sha:                                 # source drifted since it was stamped
        return hit(core.Resolution.SOURCE_STALE, sha=cur_sha, rel=src.rel), source_lines
    res = common.resolve(marker, common.haystacks(text))             # occurrence + ambiguity (shared matcher)
    if not res.found:
        return hit(core.Resolution.NOT_FOUND, sha=cur_sha, rel=src.rel), source_lines
    if res.ambiguous:
        return hit(core.Resolution.AMBIGUOUS, sha=cur_sha, rel=src.rel), source_lines
    p = query.card_path(cfg, card)                                   # freshness of the RESOLVED claim
    fm = common.front_matter(common.read(p)) if p else {}
    rb = common.fm_get(fm, "review_by")
    if rb and rb.strip():
        try:
            expires = date.fromisoformat(rb.strip())
        except ValueError:
            return hit(core.Resolution.REVIEW_DATE_INVALID, sha=cur_sha, rel=src.rel), source_lines
        if expires < (date.today() if today is None else today):
            return hit(core.Resolution.REVIEW_EXPIRED, sha=cur_sha, rel=src.rel), source_lines
    if require_stamp and not stored:
        return hit(core.Resolution.SOURCE_UNSTAMPED, sha=cur_sha, rel=src.rel), source_lines
    located = [(i, ln.strip()) for i, ln in enumerate(source_lines, 1) if marker.phrase in ln][:max_lines]
    resolution = core.Resolution.FOUND if located else core.Resolution.FOLDED_ONLY
    return hit(resolution, lines=tuple(located), sha=cur_sha, rel=src.rel), source_lines


def verify_evidence(cfg, card: str, phrase: str, *, require_stamp: bool = False,
                    today: "date | None" = None) -> core.EvidenceHit:
    """The structured, freshness- AND review-aware grounding verifier — stricter than the
    occurrence-only `verify` op. A source that is stale (stamped hash drifted) or past its
    `review_by` date does NOT ground; an UNSTAMPED source grounds unless `require_stamp=True` (a
    supervising gate should set that). `superseded_by` is NOT enforced here — it is an advisory
    re-point signal (check H WARN), not a grounding gate. Line location is case-sensitive literal."""
    return _resolve_snapshot(cfg, card, phrase, require_stamp=require_stamp, today=today)[0]


def _locate_folded(text: str, phrase: str) -> tuple[int, int] | None:
    """Best-effort 1-indexed (start,end) line span for a phrase that resolved only across line/space
    folding, so it has no single matched line. Matches the phrase's words separated by any run of
    whitespace (the common line-break fold) — case-SENSITIVELY, to match the literal resolver rather
    than locate a different-case occurrence. Returns None if it cannot be placed (hyphenation and
    smart-quote folds are not reconstructed here; the caller treats an unlocatable span as unusable)."""
    import re
    words = [re.escape(w) for w in phrase.split()]           # str.split() never yields empty tokens
    if not words:
        return None
    m = re.search(r"\s+".join(words), text)                  # case-sensitive, like the literal matcher
    if not m:
        return None
    return text.count("\n", 0, m.start()) + 1, text.count("\n", 0, m.end()) + 1


def verify_context(cfg, card: str, phrase: str, *, context_lines: int = 3, max_window: int = 40,
                   require_stamp: bool = False, today: "date | None" = None) -> core.EvidenceContext:
    """The evidence-context op: a grounding result PLUS the bounded surrounding source text a judge
    reads to score a claim against the book's own words. A stale, unstamped (when required), or
    review-expired source is `usable=False`. Shares ONE source read with the grounding check (no
    double read, no between-read drift). The window is at most `max_window` lines and always contains
    the match — its whole span when that fits, otherwise the match start (an over-long match is
    truncated at the tail). A source shorter than the window is returned in full; never a whole dump."""
    if not isinstance(context_lines, int) or isinstance(context_lines, bool) or context_lines < 0:
        raise ValueError(f"context_lines must be a non-negative int, got {context_lines!r}")
    if not isinstance(max_window, int) or isinstance(max_window, bool) or max_window < 1:
        raise ValueError(f"max_window must be a positive int, got {max_window!r}")
    hit, lines = _resolve_snapshot(cfg, card, phrase, require_stamp=require_stamp, today=today)
    base = dict(resolution=hit.resolution, card=card, rel=hit.rel, source_sha=hit.source_sha)
    if hit.resolution not in (core.Resolution.FOUND, core.Resolution.FOLDED_ONLY) or lines is None:
        return core.EvidenceContext(**base)                          # not usable: no span
    match_nums = tuple(n for n, _ in hit.lines)
    if not match_nums:                                               # folded match: locate the span
        span = _locate_folded("\n".join(lines), phrase)
        if span is None:
            return core.EvidenceContext(**base)                      # resolves but not locatable -> unusable
        match_nums = span                                            # (start, end) — not a materialized range
    m_lo, m_hi = min(match_nums), max(match_nums)
    lo = max(1, m_lo - context_lines)
    hi = min(len(lines), m_hi + context_lines)
    if hi - lo + 1 > max_window:                                    # cap the window
        span = m_hi - m_lo + 1
        if span >= max_window:                                      # the match itself fills/exceeds it —
            lo, hi = m_lo, min(len(lines), m_lo + max_window - 1)   #   anchor at the match start (tail cut)
        else:                                                       # else keep the WHOLE match + any lead
            lead = min(context_lines, max_window - span)
            lo = max(1, m_lo - lead)
            hi = min(len(lines), lo + max_window - 1)
    return core.EvidenceContext(usable=True, line_start=lo, line_end=hi, match_lines=match_nums,
                                text="\n".join(lines[lo - 1:hi]), **base)


# ---------------------------------------------------------------------------
# supervision service — review a draft. EXPERIMENTAL: the judge is a stub, so the result MUST
# self-label (never an authoritative Go/Recycle from a fake judge).
# ---------------------------------------------------------------------------
def _svc_review(cfg, params: dict) -> core.ReviewResult:
    from klode import gate                                      # lazy: only review needs the gate
    judge = params.get("judge") or gate.FixtureJudge({}, default=(7, "acceptable"))
    identity = type(judge).__name__
    if params.get("refuse_non_production"):                    # a caller may refuse non-production output
        return core.ReviewResult(CapabilityStatus.UNAVAILABLE, judge_identity=identity)
    v = gate.review_draft(cfg, params.get("draft", ""), params.get("dimension", ""),
                          judge, hurdle=int(params.get("hurdle", 60)))
    return core.ReviewResult(
        capability=CapabilityStatus.EXPERIMENTAL,
        judge_identity=identity,
        non_production=True,                                   # the stub verdict is never authoritative
        decision=v.decision, score=v.score,
        defects=tuple((ln.criterion.statement, ln.score, ln.note) for ln in v.defects),
    )


# ---------------------------------------------------------------------------
# authoring services — thin delegations to the existing engine modules (behaviour unchanged).
# (init/ingest keep their CLI-direct handlers; they are registered for parity but not routed here.)
# ---------------------------------------------------------------------------
def _svc_check(cfg, params: dict):
    from . import check as check_mod
    return check_mod.check(cfg, strict=bool(params.get("strict", False)))


def _svc_build(cfg, params: dict):
    from . import build as build_mod
    return build_mod.build(cfg, stamp=bool(params.get("stamp", False)))


def _svc_normalize(cfg, params: dict):
    from . import normalize as norm_mod
    return norm_mod.normalize(cfg, pattern=params.get("glob", "*/*.txt"),
                              apply=bool(params.get("apply", False)), stamp=params.get("stamp"))


# op-id -> service callable. registry-scoped services take `pool`; kb-scoped take `cfg`.
SERVICES = {
    "kbs.list": _svc_kbs_list,
    "lenses.list": _svc_lenses_list,
    "cards.list": _svc_cards_list,
    "search": _svc_search,
    "diagnose": _svc_diagnose,
    "consult": _svc_consult,
    "zoom": _svc_zoom,
    "verify": _svc_verify,
    "review": _svc_review,
    "check": _svc_check,
    "build": _svc_build,
    "normalize": _svc_normalize,
}


# ---------------------------------------------------------------------------
# the shared executor — the single entry point both adapters call
# ---------------------------------------------------------------------------
def execute(pool, op_id: str, kb_arg: str | None = None, params: dict | None = None) -> OpResult:
    params = params or {}
    spec = opspec.by_op_id(op_id)
    if spec is None or op_id not in SERVICES:
        raise ScopeError(f"unknown or unimplemented operation {op_id!r}")
    fn = SERVICES[op_id]
    scope = resolve_scope(pool, spec, kb_arg)

    if scope.kind == "registry":
        return OpResult(op_id, Provenance(op_id=op_id), fn(pool, params), spec.capability)

    if scope.kind == "kb":
        kid = scope.ids[0]
        cfg = pool.config(kid)                                # unknown/broken -> ConfigError (adapter handles)
        value = fn(cfg, params)
        prov = Provenance(op_id=op_id, kb=kid, source_sha=getattr(value, "source_sha", None))
        return OpResult(op_id, prov, value, spec.capability)

    # scope == kbs: fan out, one sub-result per KB, capped and error-isolated
    items: list[OpResult] = []
    ids = scope.ids
    for kid in ids[:FANOUT_CAP]:
        prov = Provenance(op_id=op_id, kb=kid)
        try:
            items.append(OpResult(op_id, prov, fn(pool.config(kid), params), spec.capability))
        except (ConfigError, OSError, UnicodeError) as e:   # one KB's config/IO/decode failure never
            items.append(OpResult(op_id, prov, str(e), CapabilityStatus.UNAVAILABLE))  # sinks the fan-out;
            #                                              a programming bug (TypeError, …) still propagates
    truncated = max(0, len(ids) - FANOUT_CAP)
    return OpResult(op_id, Provenance(op_id=op_id), core.FanOut(tuple(items), truncated), spec.capability)
