"""klode/lib/opspec.py — the operation registry: the executable form of dev-docs/SPEC-operations.md.

One declarative `OpSpec` per canonical operation: its scope, capability, and its CLI + MCP
projections (public names / compatibility aliases). This is the structural anti-drift spine — both
adapters are projected from and tested against it (see tests/test_parity.py). It is DATA: it imports
only `core` (for the capability enum), no domain service and no adapter. The op-id -> service
callable binding lives in `services.py` (SERVICES), keeping this table free of execution imports.
"""
from __future__ import annotations

from dataclasses import dataclass

from .core import CapabilityStatus


@dataclass(frozen=True)
class OpSpec:
    op_id: str
    scope: str                          # "registry" | "kb" | "kbs"
    capability: CapabilityStatus
    cli: str | None                     # CLI subcommand/verb name (None => not CLI-projected)
    mcp: tuple[str, ...] = ()           # MCP public tool name(s)/aliases (empty => not MCP-projected)
    fanout: bool = False                # kb-scoped op that fans out across all KBs when no kb is named
                                        # (discovery); False => a kb must be named when >1 is registered


_S, _E = CapabilityStatus.STABLE, CapabilityStatus.EXPERIMENTAL

# Keep this table in lock-step with the SPEC-operations.md `## Operations` table
# (tests/test_opspec.py asserts the op-id sets are equal).
OPS: tuple[OpSpec, ...] = (
    # consumption — discover (fan out across KBs when no kb is named)
    OpSpec("kbs.list",    "registry", _S, "kbs",       ("list_kbs",)),
    OpSpec("lenses.list", "kb",       _S, "lenses",    ("list_lenses",), fanout=True),
    OpSpec("cards.list",  "kb",       _S, "cards",     (),               fanout=True),
    OpSpec("search",      "kb",       _S, "search",    ("search_sources",), fanout=True),
    OpSpec("diagnose",    "kb",       _S, "diagnose",  ("diagnose",),    fanout=True),
    # consumption — retrieve
    OpSpec("consult",     "kb",       _S, "consult",   ("consult_dimension", "consult_framework")),
    OpSpec("zoom",        "kb",       _S, "zoom",      ("zoom_card",)),
    # consumption — ground
    OpSpec("verify",      "kb",       _S, "verify",    ("verify_quote",)),
    OpSpec("evidence",    "kb",       _S, "evidence",  ("retrieve_evidence",)),
    # consumption — supervise (experimental: stub judge)
    OpSpec("review",      "kb",       _E, "review",    ()),
    # authoring — maintain a KB (CLI-only)
    OpSpec("init",        "registry", _S, "init",      ()),
    OpSpec("build",       "kb",       _S, "build",     ()),
    OpSpec("check",       "kb",       _S, "check",     ()),
    OpSpec("normalize",   "kb",       _S, "normalize", ()),
    OpSpec("ingest",      "kb",       _S, "ingest",    ()),
)


def ops() -> tuple[OpSpec, ...]:
    return OPS


def by_op_id(op_id: str) -> OpSpec | None:
    return next((s for s in OPS if s.op_id == op_id), None)


def by_mcp_name(name: str) -> OpSpec | None:
    """Resolve a public MCP tool name (including a compatibility alias) to its OpSpec."""
    return next((s for s in OPS if name in s.mcp), None)


def by_cli_name(name: str) -> OpSpec | None:
    return next((s for s in OPS if s.cli == name), None)


def mcp_names() -> tuple[str, ...]:
    """Every public MCP tool name projected by the registry (across all ops)."""
    return tuple(m for s in OPS for m in s.mcp)


def _validate(specs: tuple[OpSpec, ...] = OPS) -> None:
    seen_op: set[str] = set()
    seen_mcp: set[str] = set()
    seen_cli: set[str] = set()
    for s in specs:
        if s.op_id in seen_op:
            raise ValueError(f"duplicate op-id {s.op_id!r} in the registry")
        seen_op.add(s.op_id)
        if s.scope not in ("registry", "kb", "kbs"):
            raise ValueError(f"{s.op_id}: bad scope {s.scope!r}")
        if not isinstance(s.capability, CapabilityStatus):
            raise ValueError(f"{s.op_id}: bad capability")
        if s.cli is not None:                          # else by_cli_name() would silently shadow a dup
            if s.cli in seen_cli:
                raise ValueError(f"duplicate CLI name {s.cli!r} in the registry")
            seen_cli.add(s.cli)
        for m in s.mcp:
            if m in seen_mcp:
                raise ValueError(f"duplicate MCP public name {m!r} in the registry")
            seen_mcp.add(m)


_validate()
