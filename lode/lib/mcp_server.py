"""A dependency-free MCP server exposing one library to an agent.

Why stdlib instead of the MCP SDK: lode's whole promise is stdlib-only and zero
dependency, and MCP's stdio transport is just newline-delimited JSON-RPC 2.0. Taking
the SDK would add a pinned dependency and a version treadmill to a tool whose selling
point is that it has neither. The protocol subset below (initialize / tools/list /
tools/call) is all a tool server needs.

Transport contract: **stdout carries protocol JSON and nothing else.** Every
diagnostic goes to stderr, or it corrupts the stream.

Run:  lode-mcp --config /path/to/library.toml
      (falls back to $LODLIB_CONFIG, then the nearest library.toml above cwd)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

from . import console, query
from .config import Config, ConfigError
from .pool import KBPool

PROTOCOL_VERSION = "2025-06-18"
# Self-reported MCP name (serverInfo.name): a single stable brand, "lode". It appears in `/mcp`
# listings and logs. It is deliberately NOT per-KB — clients derive the `mcp__<server>__*` tool
# prefix from the server KEY in their own MCP config (e.g. `.mcp.json`), not from serverInfo.name,
# so distinct tool namespaces come from registering each server under a distinct client key; this
# name stays "lode" regardless of which KB the process serves.
SERVER_NAME = "lode"
SERVER_VERSION = "0.1.0"

# JSON-RPC error codes we actually use
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603


# ---------------------------------------------------------------------------
# tool definitions
# ---------------------------------------------------------------------------
TOOLS = [
    # ---- the desk: distilled expertise, entered by craft problem ----
    {
        "name": "list_lenses",
        "description": (
            "START HERE. The routing table from a craft problem to the right expert. "
            "Lists every craft DIMENSION (a cross-thinker adjudication) and every "
            "FRAMEWORK (one thinker distilled), with the dimension each answers at — the "
            "names are whatever this library actually contains, so read them from here "
            "rather than guessing. Use when you have a writing problem ('this scene is "
            "flat', 'this turn isn't believable') and need to know who to ask."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "diagnose",
        "description": (
            "Route a symptom — how a draft FEELS wrong, in your own words ('this scene "
            "drags', 'the world feels like an info-dump') — to the craft dimensions worth "
            "consulting, most-relevant first. The fast path when you don't yet know which "
            "dimension to open; follow up with consult_dimension on what it returns."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symptom": {"type": "string", "description": "What feels wrong, in plain words."},
            },
            "required": ["symptom"],
        },
    },
    {
        "name": "consult_dimension",
        "description": (
            "Get the EXPERT PANEL on one craft dimension: the core question, every "
            "framework's competing answer side by side, and where they genuinely "
            "disagree (disagreements are preserved, never averaged). This is the "
            "primary tool for thinking with the library. Returns the tier "
            "(`status:`) — honor it: a `proposed` synthesis is a claim under review, "
            "not settled doctrine, and must not be presented as fact."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dimension": {
                    "type": "string",
                    "description": "A dimension stem — call list_lenses first for the exact names this library defines (they are not a fixed set). A family name like 'viewpoint' returns its members to choose from.",
                },
                "audience": {
                    "type": "string",
                    "enum": ["writer", "engine", "full"],
                    "description": "writer (default): the Craft layer only — mechanism + moves + live "
                                   "options, engine/provenance omitted. engine: Craft + the scorer mapping. "
                                   "full: the whole synthesis incl. provenance/gate.",
                },
                "section": {
                    "type": "string",
                    "description": "Optional: return only the part of the synthesis whose heading matches this text (overrides audience).",
                },
            },
            "required": ["dimension"],
        },
    },
    {
        "name": "consult_framework",
        "description": (
            "Get ONE thinker's operational framework. Sections: 'engine' (the core "
            "mechanism), 'practices' (**what to actually DO** when writing — the most "
            "useful part), 'disagreement' (who this thinker contradicts), plus "
            "'primitives', 'mechanism', 'boundary', 'stance', 'on_dimension'. Defaults "
            "to engine + practices + disagreement, which is what applying the theory "
            "requires. Every claim is grep-anchored to the source book."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "A framework id from list_lenses (e.g. frijda, scarry, sternberg), OR an author name / book title — 'wayne booth', 'the rhetoric of fiction' both resolve to Booth."},
                "sections": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Which sections to return. Omit for engine+practices+disagreement; use ['all'] for everything.",
                },
            },
            "required": ["name"],
        },
    },
    # ---- the shelf: fall back here only when the desk isn't enough ----
    {
        "name": "search_sources",
        "description": (
            "FALLBACK. Search the underlying source cards (one per book/paper) when the "
            "dimension and framework layers don't cover what you need. Prefer "
            "list_lenses / consult_dimension first — those hold the distilled expertise; "
            "this only finds raw sources. Returns card ids for zoom_card."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "One or more terms, space separated (e.g. 'emotion narrative flat scene').",
                },
                "full": {
                    "type": "boolean",
                    "description": "Also search the longer L2 'Full' bodies, not just metadata and the gist.",
                    "default": False,
                },
                "limit": {"type": "integer", "description": "Max results.", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "zoom_card",
        "description": (
            "Pull one Level of Zoom for a single source card. Levels: 'meta' "
            "(front-matter: shelf, aliases, how filled), 'thin' (1-3 sentence gist — "
            "cheapest useful read), 'full' (the outlined argument, each point "
            "grep-anchored to the source), 'content' (where the raw text lives). "
            "Prefer 'thin' first; escalate to 'full' only when you need the argument."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Card id, as returned by search_sources."},
                "level": {
                    "type": "string",
                    "enum": ["meta", "thin", "full", "content"],
                    "description": "Which level to pull.",
                    "default": "thin",
                },
            },
            "required": ["id"],
        },
    },
    {
        "name": "verify_quote",
        "description": (
            "Check that a phrase genuinely appears in a card's underlying source text. "
            "This is the anti-hallucination tool: before attributing a claim to a "
            "source, verify the wording actually occurs. Tolerant of line wrapping, "
            "hyphenation, and smart quotes. Returns the matching source lines, or "
            "reports that the phrase does NOT resolve."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Card id whose source should be searched."},
                "phrase": {"type": "string", "description": "The exact phrase to verify."},
                "max_lines": {"type": "integer", "description": "Max matching lines to return.", "default": 10},
            },
            "required": ["id", "phrase"],
        },
    },
    # ---- the registry: which knowledge bases this server serves ----
    {
        "name": "list_kbs",
        "description": (
            "List the knowledge bases THIS server serves — each with its id and a one-line "
            "description of what it covers. A passive catalog: it tells you which KBs exist so "
            "you can choose one whose description fits your task, and every id it lists is "
            "addressable via `kb`. It does not choose for you or order them by importance."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# ---------------------------------------------------------------------------
# tool implementations — each returns a plain string for a text content block
# ---------------------------------------------------------------------------
def _tool_list_lenses(cfg: Config, args: dict) -> str:
    dims, fws = query.lenses(cfg)
    if not dims and not fws:
        return ("This library has no frameworks/syntheses layer enabled "
                "([frameworks] in library.toml). Only source cards are available.")
    out = ["# Craft dimensions — the expert panels (consult_dimension)", ""]
    for d in dims:
        out.append(f"- **{d.name}**  [tier: {d.status}]")
        if d.summary:
            out.append(f"    core question: {d.summary}")
    out += ["", "# Frameworks — one thinker each (consult_framework)", ""]
    by_dim: dict[str, list[str]] = {}
    for f in fws:
        by_dim.setdefault(f.dimension.split("—")[0].strip(), []).append(f.name)
    for dim, names in sorted(by_dim.items()):
        out.append(f"- **{dim}**: {', '.join(sorted(names))}")
    out += ["", "Route a writing problem to a dimension first; drop to a single "
                "framework when you need its specific moves."]
    return "\n".join(out)


def _tool_diagnose(cfg: Config, args: dict) -> str:
    result = query.diagnose(cfg, str(args.get("symptom", "")))
    if result is None:
        return ("This library has no diagnostics map (`_syntheses/_diagnostics.md`). "
                "Use list_lenses to pick a dimension directly.")
    if not result:
        return "No dimension matched that symptom. Try list_lenses, or search_sources for raw material."
    out = ["# Symptom → dimensions (most-relevant first)", ""]
    for dn, q in result:
        out.append(f"- **{dn}** — consult_dimension(dimension=\"{dn}\")")
        if q:
            out.append(f"    {q}")
    return "\n".join(out)


def _resolve_or_msg(cfg: Config, raw: str):
    """Resolve a free-text name (stem / author / book title) to a lens — parity with the CLI's
    `lode consult`. Returns (match, message): exactly one is non-None; `message` carries the shared
    ambiguity / no-match note built in `query.resolve` (one source of truth, parity defect §6)."""
    r = query.resolve(cfg, raw)
    if r.outcome in ("ambiguous", "none"):
        return None, r.note
    return r.match, None


def _source_summary(cfg: Config, cid: str) -> str:
    """A book with no framework lens — hand back its source-card summary (L1/L2)."""
    out = [f"# {query.card_title(cfg, cid)} — source card (no framework lens)"]
    shown = False
    for lvl in ("thin", "full"):
        b = query.body(cfg, cid, lvl)
        if b and not (b.strip().startswith("_(L") and "owed" in b.lower()):   # skip the build stub
            out.append(f"## {lvl.title()}\n\n{b}")
            shown = True
    if not shown:
        out.append("_(no L1/L2 written yet — an un-distilled source)_")
    out.append(f"_(zoom the raw source with zoom_card(id=\"{cid}\") / verify_quote)_")
    return "\n\n".join(out)


def _tool_consult_dimension(cfg: Config, args: dict) -> str:
    raw = str(args.get("dimension", "")).strip()
    section = str(args.get("section", "")).strip()
    audience = str(args.get("audience", "writer")).strip().lower()
    proj = "sections" if section else (audience if audience in ("writer", "engine", "full") else "writer")
    res = console.consult(cfg, console.ConsultRequest(
        raw, projection=proj, sections=(section,) if section else ()))
    if res.outcome in ("ambiguous", "none"):
        return res.note
    if res.outcome != "dimension":                    # protocol: this tool serves dimensions only
        nm = res.framework["name"] if res.framework else res.source_id
        return (f"`{raw}` resolves to a {res.outcome} (`{nm}`), not a craft dimension — "
                f"call consult_framework(name=\"{nm}\").")
    v = res.view
    head = [f"# Dimension — {v.name}",
            f"**Tier: `{v.status}`** — "
            + ("a claim staged for review, NOT settled doctrine. Present it as a "
               "proposal and say so." if v.status != "canonical"
               else "human-approved; usable as settled."),
            f"**Core question:** {v.core_question}" if v.core_question else ""]
    if section:                                       # explicit section overrides audience
        body = "\n\n".join(f"## {h}\n\n{b}" for h, b in res.selected) if res.selected else v.body
    elif audience == "writer" and "craft" in v.secs:
        body = (f"## Craft\n\n{res.selected[0][1]}\n\n"
                "_(engine mapping + provenance omitted — `audience:\"full\"` for the whole synthesis)_")
    elif audience == "engine" and "craft" in v.secs:
        if len(res.selected) == 1 and res.selected[0][0] == "craft":   # no engine-mapping section matched
            body = (f"## Craft\n\n{res.selected[0][1]}\n\n"
                    "_(no engine-mapping section found; `audience:\"full\"` for the whole synthesis)_")
        else:
            body = "\n\n".join(f"## {h}\n\n{b}" for h, b in res.selected)
    else:                                             # full, or writer/engine with no Craft layer yet
        if v.cards:
            head.append(f"**Adjudicates across:** {v.cards}")
        if v.gate:
            head.append(f"**Skeptic-gate row:** {v.gate}")
        body = v.body
    return "\n\n".join([x for x in head if x]) + "\n\n---\n\n" + body


_DEFAULT_FW_SECTIONS = ["engine", "practices", "disagreement"]


def _tool_consult_framework(cfg: Config, args: dict) -> str:
    raw = str(args.get("name", "")).strip()
    m, msg = _resolve_or_msg(cfg, raw)
    if msg:
        return msg
    if m.kind == "dimension":
        return (f"`{raw}` resolves to a craft dimension (`{m.name}`), not a single thinker — "
                f"call consult_dimension(dimension=\"{m.name}\").")
    if m.kind == "source":                            # a book without a framework lens
        return _source_summary(cfg, m.name)
    fw = query.framework(cfg, m.name)
    if fw is None:
        return f"No framework {raw!r}."
    want = args.get("sections") or _DEFAULT_FW_SECTIONS
    if isinstance(want, str):
        want = [want]
    if "all" in [str(w).lower() for w in want]:
        want = list(fw["sections"])
    out = [f"# {fw['title'] or fw['name']}",
           f"**Dimension:** {fw['dimension']}"]
    if fw["aliases"]:
        out.append(f"**Aliases:** {fw['aliases']}")
    out.append("")
    shown: list[str] = []
    for key in want:                      # substring match, matching the CLI `lode consult --section`
        k = str(key).lower()
        for s in fw["sections"]:
            if k in s and s not in shown:
                out.append(f"## {s}\n\n{fw['sections'][s]}\n")
                shown.append(s)
    missing = [str(k) for k in want if not any(str(k).lower() in s for s in fw["sections"])]
    if missing:
        out.append(f"_(no such section: {', '.join(missing)}; "
                   f"available: {', '.join(fw['sections'])})_")
    return "\n".join(out)


def _tool_search(cfg: Config, args: dict) -> str:
    terms = str(args.get("query", "")).split()
    if not terms:
        return "No search terms given."
    limit = int(args.get("limit", 10))
    hits, total = query.search(cfg, terms, full=bool(args.get("full", False)), limit=limit)
    if not hits:
        return f"No cards match {' '.join(terms)!r}."
    lines = [f"{len(hits)} of {total} matching cards (most relevant first):", ""]
    for h in hits:
        lines.append(f"- {h.id}  [{h.zoom} · {h.shelf}]  score={h.score:.2f}")
        if h.gist:
            lines.append(f"    {h.gist}")
    if total > len(hits):
        lines.append("")
        lines.append(f"({total - len(hits)} more — raise `limit` to see them.)")
    return "\n".join(lines)


def _tool_zoom(cfg: Config, args: dict) -> str:
    card_id = str(args.get("id", "")).strip()
    level = str(args.get("level", "thin"))
    if not query.card_path(cfg, card_id):
        return f"No card with id {card_id!r}. Use search_sources to find valid ids."
    if level == "meta":
        return f"# {card_id} (meta)\n\n{query.meta(cfg, card_id)}"
    if level in ("thin", "full"):
        body = query.body(cfg, card_id, level)
        if not body:
            return (f"Card {card_id!r} has no '{level}' section yet "
                    f"(it is only filled to an earlier level).")
        return f"# {query.card_title(cfg, card_id)}  ({level})\n\n{body}"
    if level == "content":
        src = query.source_of(cfg, card_id)
        if src is None or src.rel is None:
            return f"Card {card_id!r} records no source file."
        if not src.installed:
            return (f"Source not installed on this machine: {src.rel}\n"
                    f"(the corpus is git-ignored by design; cards remain usable)")
        return (f"{src.rel} — {src.size} bytes, grep-ready.\n"
                f"Use verify_quote to check a specific phrase against it; the raw text "
                f"is deliberately not dumped here.")
    return f"Unknown level {level!r}. Use meta, thin, full, or content."


def _tool_verify(cfg: Config, args: dict) -> str:
    card_id = str(args.get("id", "")).strip()
    phrase = str(args.get("phrase", ""))
    if not query.card_path(cfg, card_id):
        return f"No card with id {card_id!r}."
    if not phrase:
        return "No phrase given to verify."
    v = query.verify(cfg, card_id, phrase, max_lines=int(args.get("max_lines", 10)))
    if v is None:
        return (f"Source for {card_id!r} is not installed on this machine, so the "
                f"phrase cannot be verified here.")
    if not v.found:
        return (f"NOT FOUND — {phrase!r} does not resolve in {v.rel}.\n"
                f"Do not attribute this wording to {card_id}.")
    if v.folded_only:
        return (f"VERIFIED — {phrase!r} resolves in {v.rel}, but only across line or "
                f"hyphenation folding, so it sits on no single raw line.")
    body = "\n".join(f"  {n}: {ln}" for n, ln in v.lines)
    return f"VERIFIED — {phrase!r} occurs in {v.rel}:\n{body}"


DISPATCH = {
    # desk — distilled expertise, entered by craft problem
    "list_lenses": _tool_list_lenses,
    "diagnose": _tool_diagnose,
    "consult_dimension": _tool_consult_dimension,
    "consult_framework": _tool_consult_framework,
    # shelf — fall back to raw sources
    "search_sources": _tool_search,
    "zoom_card": _tool_zoom,
    "verify_quote": _tool_verify,
}   # NB list_kbs is NOT here: it is registry/pool-scoped, not per-cfg, so the router handles it.


# ---------------------------------------------------------------------------
# JSON-RPC plumbing
# ---------------------------------------------------------------------------
def _send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(req_id, result: dict) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "result": result})


def _error(req_id, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


# ---------------------------------------------------------------------------
# multi-KB routing — the pool addresses many KBs. Grounding tools always name their KB;
# discovery tools can fan out across all KBs. All multiplexing lives here in the router;
# the underlying `_tool_x(cfg, args)` functions stay single-cfg and KB-agnostic.
# ---------------------------------------------------------------------------
_GROUNDING = ("consult_dimension", "consult_framework", "zoom_card", "verify_quote")
_DISCOVERY = ("search_sources", "list_lenses", "diagnose")
_FANOUT_CAP = 10       # max KBs a single fan-out renders; the rest are announced, not dumped

# The `kb` selector, added to every KB-scoped tool (list_kbs is registry-scoped, so it is skipped).
# `kb` is intentionally NOT placed in any tool's `required` array: grounding requires it only when
# more than one KB is registered — the router enforces that at call time — which keeps single-KB
# ergonomics and the existing `required` sets (e.g. consult_dimension → {"dimension"}) unchanged.
_KB_GROUNDING = {"type": "string",
                 "description": "The KB id to query (see list_kbs). Optional when only one KB is "
                                "registered; required when several are."}
_KB_DISCOVERY = {"type": "string",
                 "description": "Optional KB id to scope to (see list_kbs). Omit or pass \"*\" to "
                                "fan out across all registered KBs, tagged by id."}
for _t in TOOLS:
    if _t["name"] in _GROUNDING:
        _t["inputSchema"].setdefault("properties", {})["kb"] = _KB_GROUNDING
    elif _t["name"] in _DISCOVERY:
        _t["inputSchema"].setdefault("properties", {})["kb"] = _KB_DISCOVERY


def _route_grounding(pool: KBPool, fn, args: dict) -> tuple[str, bool]:
    """A grounding tool addresses exactly one KB. `kb` defaults to the sole KB (single-KB pool)
    and is required when more than one is registered. Output is ALWAYS prefixed with the resolved
    `[kb-id]` provenance tag, so every grounded result names its KB. Returns (text, is_error)."""
    kb = args.get("kb") or pool.default
    if not kb:
        return (f"No `kb` given and this server hosts multiple KBs. "
                f"Pass `kb` (one of: {', '.join(pool.ids()) or '(none registered)'}).", True)
    try:
        cfg = pool.config(kb)
    except ConfigError as e:                      # unknown id, or a registered-but-broken KB
        return (str(e), True)
    return (f"[{kb}]\n{fn(cfg, args)}", False)


def _route_discovery(pool: KBPool, fn, args: dict) -> tuple[str, bool]:
    """A discovery tool helps find where to look. An explicit `kb` scopes to it (tagged); omitting
    `kb` (or `kb="*"`) fans out across all registered KBs, each block `[kb-id]`-tagged, in sorted-id
    order, capped and error-isolated. A single-KB pool with no `kb` returns untagged output,
    byte-identical to a direct call. Returns (text, is_error)."""
    kb = args.get("kb")
    if kb and kb != "*":
        try:
            cfg = pool.config(kb)
        except ConfigError as e:
            return (str(e), True)
        return (f"[{kb}]\n{fn(cfg, args)}", False)
    ids = pool.ids()
    if not ids:
        return ("No KBs are registered.", False)
    if len(ids) == 1:
        return (fn(pool.config(ids[0]), args), False)          # untagged — byte-identical to direct
    blocks: list[str] = []
    for kid in ids[:_FANOUT_CAP]:
        try:
            out = fn(pool.config(kid), args)
        except ConfigError as e:                                # one broken KB never sinks the fan-out
            out = f"(unavailable: {e})"
        blocks.append(f"[{kid}]\n{out}")
    if len(ids) > _FANOUT_CAP:
        blocks.append(f"… {len(ids) - _FANOUT_CAP} more KB(s) not shown — name a `kb` to target one.")
    return ("\n\n".join(blocks), False)


def _route_list_kbs(pool: KBPool) -> str:
    """The passive catalog of the KBs THIS server serves (id + self-description), sourced from the
    pool — so every id it lists is addressable via `kb`, and it can never diverge from what the
    server actually serves. Lists and describes; never ranks or recommends."""
    infos = pool.catalog()
    if not infos:
        return ("No KBs are registered. Start the server with a registry manifest that lists at "
                "least one [[kb]]:  lode-mcp --registry <path>.")
    lines = ["Registered knowledge bases — id and what each covers. "
             "Each KB describes itself; choose the one whose description fits your task.", ""]
    for i in infos:
        detail = (i.description or "(no description)") if i.ok else f"(unavailable: {i.error})"
        lines.append(f"- {i.id} — {detail}")
    return "\n".join(lines)


def handle(pool: KBPool, msg: dict) -> None:
    """Handle one JSON-RPC message. Notifications (no `id`) get no response."""
    method = msg.get("method")
    req_id = msg.get("id")
    is_notification = "id" not in msg

    if method == "initialize":
        # Echo the client's protocol version when it sends one; that keeps us
        # compatible with clients newer or older than our own constant.
        client_version = (msg.get("params") or {}).get("protocolVersion")
        _result(req_id, {
            "protocolVersion": client_version or PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
        return

    if is_notification:
        return  # notifications/initialized and friends need no reply

    if method == "ping":
        _result(req_id, {})
        return

    if method == "tools/list":
        _result(req_id, {"tools": TOOLS})
        return

    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            if name == "list_kbs":
                text, is_error = _route_list_kbs(pool), False       # pool-sourced catalog
            elif name in _GROUNDING:
                text, is_error = _route_grounding(pool, DISPATCH[name], args)
            elif name in _DISCOVERY:
                text, is_error = _route_discovery(pool, DISPATCH[name], args)
            else:
                text, is_error = f"Unknown tool: {name!r}", True
            _result(req_id, {"content": [{"type": "text", "text": text}], "isError": is_error})
        except Exception:
            # A tool failure is reported to the model, not raised — a crashed server
            # would take down every later call in the session.
            print(traceback.format_exc(), file=sys.stderr)
            _result(req_id, {
                "content": [{"type": "text", "text": f"Tool {name!r} failed:\n{traceback.format_exc(limit=3)}"}],
                "isError": True,
            })
        return

    _error(req_id, METHOD_NOT_FOUND, f"Method not found: {method}")


def _load_config(explicit: str | None) -> Config:
    """--config, else $LODLIB_CONFIG, else the nearest library.toml above cwd."""
    path = explicit or os.environ.get("LODLIB_CONFIG")
    return Config.load(Path(path) if path else None)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="lode-mcp", description="MCP server for one or many lode KBs.")
    p.add_argument("-c", "--config", help="serve ONE KB: path to its library.toml "
                                          "(else $LODLIB_CONFIG, else nearest)")
    p.add_argument("-r", "--registry", help="serve MANY KBs: path to a registry manifest "
                                            "(mutually exclusive with --config)")
    args = p.parse_args(argv)

    if args.config and args.registry:
        print("lode-mcp: pass --config OR --registry, not both", file=sys.stderr)
        return 2
    try:
        if args.registry:
            pool = KBPool.from_registry(args.registry)
            summary = f"{len(pool.ids())} KB(s): {', '.join(pool.ids()) or '(none)'}"
        else:
            cfg = _load_config(args.config)
            pool = KBPool.single(cfg)
            summary = f"1 KB: {cfg.id} ({cfg.config_path})"
    except ConfigError as e:
        print(f"lode-mcp: config error: {e}", file=sys.stderr)
        return 2
    print(f"lode-mcp: serving {summary}", file=sys.stderr)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            _error(None, PARSE_ERROR, f"Parse error: {e}")
            continue
        if not isinstance(msg, dict):
            _error(None, INVALID_REQUEST, "Request must be a JSON object")
            continue
        try:
            handle(pool, msg)
        except Exception:
            print(traceback.format_exc(), file=sys.stderr)
            if "id" in msg:
                _error(msg.get("id"), INTERNAL_ERROR, "Internal server error")
    return 0


if __name__ == "__main__":
    sys.exit(main())
