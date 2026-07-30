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

from . import console, core, opspec, query, services
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
INVALID_PARAMS = -32602
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


_DEFAULT_FW_SECTIONS = ["engine", "practices", "disagreement"]




# ---------------------------------------------------------------------------
# registry projection — every tool routes through services.execute (the shared core), and these
# renderers format the STRUCTURED OpResult into MCP text. Provenance ([kb] tag) is rendered from
# the core result, never re-derived. The CLI renders the SAME OpResults as JSON — one path, two skins.
# ---------------------------------------------------------------------------
def _params_for(name: str, args: dict) -> dict:
    if name == "consult_dimension":
        section = str(args.get("section", "")).strip()
        audience = str(args.get("audience", "writer")).strip().lower()
        proj = "sections" if section else (audience if audience in ("writer", "engine", "full") else "writer")
        return {"name": str(args.get("dimension", "")).strip(),
                "projection": proj, "sections": (section,) if section else ()}
    if name == "consult_framework":
        return {"name": str(args.get("name", "")).strip(), "projection": "writer"}
    if name == "search_sources":
        return {"terms": str(args.get("query", "")).split(),
                "full": bool(args.get("full", False)), "limit": int(args.get("limit", 10))}
    if name == "zoom_card":
        return {"id": str(args.get("id", "")).strip(), "level": str(args.get("level", "thin"))}
    if name == "verify_quote":
        return {"card": str(args.get("id", "")).strip(), "phrase": str(args.get("phrase", "")),
                "max_lines": int(args.get("max_lines", 10))}
    if name == "diagnose":
        return {"symptom": str(args.get("symptom", ""))}
    return {}


def _r_list_kbs(result, args):
    infos = result.value
    if not infos:
        return ("No KBs are registered. Start the server with a registry manifest that lists at "
                "least one [[kb]]:  lode-mcp --registry <path>.")
    lines = ["Registered knowledge bases — id and what each covers. "
             "Each KB describes itself; choose the one whose description fits your task.", ""]
    for i in infos:
        detail = (i.description or "(no description)") if i.ok else f"(unavailable: {i.error})"
        lines.append(f"- {i.id} — {detail}")
    return "\n".join(lines)


def _r_list_lenses(result, args):
    dims, fws = result.value["dimensions"], result.value["frameworks"]
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


def _r_diagnose(result, args):
    res = result.value
    if res is None:
        return ("This library has no diagnostics map (`_syntheses/_diagnostics.md`). "
                "Use list_lenses to pick a dimension directly.")
    if not res:
        return "No dimension matched that symptom. Try list_lenses, or search_sources for raw material."
    out = ["# Symptom → dimensions (most-relevant first)", ""]
    for dn, q in res:
        out.append(f"- **{dn}** — consult_dimension(dimension=\"{dn}\")")
        if q:
            out.append(f"    {q}")
    return "\n".join(out)


def _r_search(result, args):
    hits, total = result.value["hits"], result.value["total"]
    terms = str(args.get("query", "")).split()
    if not terms:
        return "No search terms given."
    if not hits:
        return f"No cards match {' '.join(terms)!r}."
    lines = [f"{len(hits)} of {total} matching cards (most relevant first):", ""]
    for h in hits:
        lines.append(f"- {h.id}  [{h.zoom} · {h.shelf}]  score={h.score:.2f}")
        if h.gist:
            lines.append(f"    {h.gist}")
    if total > len(hits):
        lines += ["", f"({total - len(hits)} more — raise `limit` to see them.)"]
    return "\n".join(lines)


def _r_zoom(result, args):
    c = result.value
    cid = str(args.get("id", "")).strip()
    if c is None:
        return f"No card with id {cid!r}. Use search_sources to find valid ids."
    level = c.level
    if level == "meta":
        return f"# {cid} (meta)\n\n{c.body}"
    if level in ("thin", "full"):
        if not c.body:
            return f"Card {cid!r} has no '{level}' section yet (it is only filled to an earlier level)."
        return f"# {c.title}  ({level})\n\n{c.body}"
    if level == "content":
        s = c.source
        if s is None or s.rel is None:
            return f"Card {cid!r} records no source file."
        if not s.installed:
            return (f"Source not installed on this machine: {s.rel}\n"
                    f"(the corpus is git-ignored by design; cards remain usable)")
        return (f"{s.rel} — {s.size} bytes, grep-ready.\n"
                f"Use verify_quote to check a specific phrase against it; the raw text "
                f"is deliberately not dumped here.")
    return f"Unknown level {level!r}. Use meta, thin, full, or content."


def _r_verify(result, args):
    e = result.value
    phrase = str(args.get("phrase", ""))
    card = str(args.get("id", "")).strip()
    if not phrase:
        return "No phrase given to verify."
    R = core.Resolution
    if e.resolution is R.SOURCE_NOT_INSTALLED:
        return (f"Source for {card!r} is not installed on this machine (or no such card), so the "
                f"phrase cannot be verified here.")
    if e.resolution is R.SOURCE_STALE:
        return (f"STALE — the source {e.rel} changed since {card!r} was stamped; the citation's "
                f"baseline moved, so re-verify before trusting it.")
    if e.resolution is R.NOT_FOUND:
        return f"NOT FOUND — {phrase!r} does not resolve in {e.rel}.\nDo not attribute this wording to {card}."
    if e.resolution is R.AMBIGUOUS:
        return (f"AMBIGUOUS — {phrase!r} resolves in more than one place in {e.rel}; "
                f"pin the intended occurrence with surrounding context.")
    if e.resolution is R.FOLDED_ONLY:
        return (f"VERIFIED — {phrase!r} resolves in {e.rel}, but only across line or "
                f"hyphenation folding, so it sits on no single raw line.")
    body = "\n".join(f"  {n}: {ln}" for n, ln in e.lines)
    return f"VERIFIED — {phrase!r} occurs in {e.rel}:\n{body}"


def _source_summary_from(v: "core.SourceCardResult") -> str:
    out = [f"# {v.title} — source card (no framework lens)"]
    shown = False
    for lvl, b in (("Thin", v.thin), ("Full", v.full)):
        if b and not (b.strip().startswith("_(L") and "owed" in b.lower()):
            out.append(f"## {lvl}\n\n{b}")
            shown = True
    if not shown:
        out.append("_(no L1/L2 written yet — an un-distilled source)_")
    out.append(f"_(zoom the raw source with zoom_card(id=\"{v.id}\") / verify_quote)_")
    return "\n\n".join(out)


def _r_consult_dimension(result, args):
    v = result.value
    raw = str(args.get("dimension", "")).strip()
    section = str(args.get("section", "")).strip()
    audience = str(args.get("audience", "writer")).strip().lower()
    if isinstance(v, core.Note):
        return v.message
    if not isinstance(v, core.DimensionResult):
        nm = v.name if isinstance(v, core.FrameworkResult) else v.id
        kind = "framework" if isinstance(v, core.FrameworkResult) else "source"
        return (f"`{raw}` resolves to a {kind} (`{nm}`), not a craft dimension — "
                f"call consult_framework(name=\"{nm}\").")
    head = [f"# Dimension — {v.name}",
            f"**Tier: `{v.status}`** — "
            + ("a claim staged for review, NOT settled doctrine. Present it as a "
               "proposal and say so." if v.status != "canonical" else "human-approved; usable as settled."),
            f"**Core question:** {v.core_question}" if v.core_question else ""]
    if section:
        body = "\n\n".join(f"## {h}\n\n{b}" for h, b in v.selected) if v.selected else v.body
    elif audience == "writer" and "craft" in v.secs:
        body = (f"## Craft\n\n{v.selected[0][1]}\n\n"
                "_(engine mapping + provenance omitted — `audience:\"full\"` for the whole synthesis)_")
    elif audience == "engine" and "craft" in v.secs:
        if len(v.selected) == 1 and v.selected[0][0] == "craft":
            body = (f"## Craft\n\n{v.selected[0][1]}\n\n"
                    "_(no engine-mapping section found; `audience:\"full\"` for the whole synthesis)_")
        else:
            body = "\n\n".join(f"## {h}\n\n{b}" for h, b in v.selected)
    else:
        if v.cards:
            head.append(f"**Adjudicates across:** {v.cards}")
        if v.gate:
            head.append(f"**Skeptic-gate row:** {v.gate}")
        body = v.body
    return "\n\n".join([x for x in head if x]) + "\n\n---\n\n" + body


def _r_consult_framework(result, args):
    v = result.value
    raw = str(args.get("name", "")).strip()
    if isinstance(v, core.Note):
        return v.message
    if isinstance(v, core.DimensionResult):
        return (f"`{raw}` resolves to a craft dimension (`{v.name}`), not a single thinker — "
                f"call consult_dimension(dimension=\"{v.name}\").")
    if isinstance(v, core.SourceCardResult):
        return _source_summary_from(v)
    want = args.get("sections") or _DEFAULT_FW_SECTIONS
    if isinstance(want, str):
        want = [want]
    if "all" in [str(w).lower() for w in want]:
        want = list(v.sections)
    out = [f"# {v.title or v.name}", f"**Dimension:** {v.dimension}"]
    if v.aliases:
        out.append(f"**Aliases:** {v.aliases}")
    out.append("")
    shown: list[str] = []
    for key in want:
        k = str(key).lower()
        for s in v.sections:
            if k in s and s not in shown:
                out.append(f"## {s}\n\n{v.sections[s]}\n")
                shown.append(s)
    missing = [str(k) for k in want if not any(str(k).lower() in s for s in v.sections)]
    if missing:
        out.append(f"_(no such section: {', '.join(missing)}; available: {', '.join(v.sections)})_")
    return "\n".join(out)


RENDERERS = {
    "list_kbs": _r_list_kbs, "list_lenses": _r_list_lenses, "diagnose": _r_diagnose,
    "search_sources": _r_search, "zoom_card": _r_zoom, "verify_quote": _r_verify,
    "consult_dimension": _r_consult_dimension, "consult_framework": _r_consult_framework,
}


def _dispatch_mcp(pool: KBPool, name: str, args: dict) -> tuple[str, bool]:
    """Route a tool call through services.execute, then render + tag. (text, is_error)."""
    spec = opspec.by_mcp_name(name)
    render = RENDERERS.get(name)
    if spec is None or render is None:
        return (f"Unknown tool: {name!r}", True)
    kb_arg = args.get("kb")
    try:
        result = services.execute(pool, spec.op_id, kb_arg, _params_for(name, args))
    except ConfigError as e:
        return (str(e), True)
    v = result.value
    if isinstance(v, core.FanOut):
        blocks = []
        for it in v.items:
            body = (f"(unavailable: {it.value})" if it.capability is core.CapabilityStatus.UNAVAILABLE
                    else render(it, args))
            blocks.append(f"[{it.provenance.kb}]\n{body}")
        if v.truncated:
            blocks.append(f"… {v.truncated} more KB(s) not shown — name a `kb` to target one.")
        return ("\n\n".join(blocks), False)
    body = render(result, args)
    if spec.scope == "registry":
        return (body, False)                                  # list_kbs — no tag
    tag = (not spec.fanout) or bool(kb_arg and kb_arg != "*")  # grounding always; discovery only if explicit
    return (f"[{result.provenance.kb}]\n{body}" if tag else body, False)


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


def handle(pool: KBPool, msg: dict) -> None:
    """Handle one JSON-RPC message. Notifications (no `id`) get no response."""
    method = msg.get("method")
    req_id = msg.get("id")
    is_notification = "id" not in msg

    if method == "initialize":
        # Echo the client's protocol version when it sends a well-formed one; that keeps us
        # compatible with clients newer or older than our own constant. A missing or non-string
        # version falls back to ours rather than being echoed as a false claim of support.
        params = msg.get("params")
        cv = params.get("protocolVersion") if isinstance(params, dict) else None
        _result(req_id, {
            "protocolVersion": cv if isinstance(cv, str) and cv else PROTOCOL_VERSION,
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
        params = msg.get("params")
        if not isinstance(params, dict):
            _error(req_id, INVALID_PARAMS, "params must be an object")
            return
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(name, str) or not name:
            _error(req_id, INVALID_PARAMS, "tool name must be a non-empty string")
            return
        if not isinstance(args, dict):
            _error(req_id, INVALID_PARAMS, "tool arguments must be an object")
            return
        try:
            text, is_error = _dispatch_mcp(pool, name, args)
            _result(req_id, {"content": [{"type": "text", "text": text}], "isError": is_error})
        except Exception:
            # A tool failure is reported to the model, not raised — a crashed server
            # would take down every later call in the session. The traceback (which discloses
            # absolute paths + internals) goes to stderr only; the client gets a stable message.
            print(traceback.format_exc(), file=sys.stderr)
            _result(req_id, {
                "content": [{"type": "text", "text": f"Tool {name!r} failed (see server logs)."}],
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
