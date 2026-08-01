"""klode/lib/core.py — the canonical value types the operations model rests on.

A stdlib-only LEAF: it imports no other `klode` module, no domain service, and no adapter
(`cli`/`mcp_server`), so it sits at the very bottom of the layering. Services build these from the
engine's own values (e.g. a `SourceInfo` is assembled from `query.Source`); the core never reaches up.

Everything here is frozen. Provenance is STRUCTURED and lives in every result — the MCP `[kb]` text
tag and the CLI `provenance` JSON field are adapter *renderings* of it, never the contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CapabilityStatus(str, Enum):
    STABLE = "stable"
    EXPERIMENTAL = "experimental"
    UNAVAILABLE = "unavailable"


class Resolution(str, Enum):
    """A grounding result records textual OCCURRENCE, never claim truth/entailment."""
    FOUND = "found"
    AMBIGUOUS = "ambiguous"
    FOLDED_ONLY = "folded-only"
    SOURCE_STALE = "source-stale"
    SOURCE_NOT_INSTALLED = "source-not-installed"
    NOT_FOUND = "not-found"


@dataclass(frozen=True)
class Provenance:
    op_id: str
    kb: str | None = None                    # the KB id (None for registry-scoped ops)
    op_version: str = "1"
    source_sha: str | None = None            # the source hash actually used (grounding)
    source_version: str | None = None
    policy: str | None = None


@dataclass(frozen=True)
class Scope:
    """Explicit scope — not every verb is KB-scoped. Build via Scope.registry()/kb(id)/kbs(ids)."""
    kind: str                                # "registry" | "kb" | "kbs"
    ids: tuple[str, ...] = ()

    def __post_init__(self):
        if self.kind not in ("registry", "kb", "kbs"):
            raise ValueError(f"unknown scope kind {self.kind!r}")
        if self.kind == "kb" and (len(self.ids) != 1 or not self.ids[0]):
            raise ValueError("scope kb requires exactly one non-empty id")
        if self.kind == "kbs" and (not self.ids or not all(self.ids)):
            raise ValueError("scope kbs requires at least one non-empty id")
        if self.kind == "registry" and self.ids:
            raise ValueError("scope registry takes no ids")

    @classmethod
    def registry(cls) -> "Scope":
        return cls("registry")

    @classmethod
    def kb(cls, kb_id: str) -> "Scope":
        return cls("kb", (kb_id,))

    @classmethod
    def kbs(cls, ids) -> "Scope":
        return cls("kbs", tuple(ids))


# --- Lens results: a DISCRIMINATED union (distinct types, not a magic `outcome` string) ---
@dataclass(frozen=True)
class DimensionResult:
    name: str
    status: str
    core_question: str
    craft: str                               # the writer-projected Craft layer
    cards: str                               # raw `cards:` panel string
    body: str                                # full synthesis body
    gate: str | None = None                  # the skeptic-gate row, if staged
    secs: dict = field(default_factory=dict)         # lowercased-heading -> body
    selected: tuple = ()                     # the projected (heading, body) pairs for this request


@dataclass(frozen=True)
class FrameworkResult:
    name: str
    dimension: str
    title: str
    aliases: str
    sections: dict


@dataclass(frozen=True)
class SourceCardResult:
    id: str
    title: str
    thin: str | None
    full: str | None


@dataclass(frozen=True)
class Note:
    """A resolution that landed on no single lens — `ambiguous` or `none` — with the shared message."""
    outcome: str
    message: str


# --- Source (the raw evidence, a noun distinct from Card) + Card content ---
@dataclass(frozen=True)
class SourceInfo:
    rel: str | None
    installed: bool
    size: int | None = None
    sha256: str | None = None                # current on-disk hash, for grounding freshness


@dataclass(frozen=True)
class CardContent:
    id: str
    level: str                               # meta | thin | full | content
    title: str
    body: str | None = None                  # projected body (None when that level is empty)
    source: SourceInfo | None = None         # populated for level == content


# --- EvidenceHit: the grounding result (occurrence, never entailment) ---
@dataclass(frozen=True)
class EvidenceHit:
    resolution: Resolution
    phrase: str
    card: str | None = None
    rel: str | None = None
    lines: tuple[tuple[int, str], ...] = ()
    source_sha: str | None = None            # the source hash the check ran against (freshness)
    occurrence_only: bool = True             # a hard reminder: found != claim verified

    @property
    def found(self) -> bool:
        # a folded-only hit IS a genuine occurrence (it resolves, just not on one raw line)
        return self.resolution in (Resolution.FOUND, Resolution.FOLDED_ONLY)


# --- ReviewResult: capability-gated supervision (never an authoritative stub verdict) ---
@dataclass(frozen=True)
class ReviewResult:
    capability: CapabilityStatus
    judge_identity: str | None = None
    non_production: bool = True
    decision: str | None = None              # None when capability is UNAVAILABLE (no verdict body)
    score: int | None = None
    defects: tuple = ()


# --- the generic result envelope every service returns ---
@dataclass(frozen=True)
class OpResult:
    op_id: str
    provenance: Provenance
    value: object                            # a list, or one of the noun payloads above
    capability: CapabilityStatus = CapabilityStatus.STABLE


@dataclass(frozen=True)
class FanOut:
    """A discovery op run across several KBs: one sub-result per KB (each with its own provenance),
    plus how many KBs were dropped by the cap. An op's `value` is a FanOut only in the kbs scope."""
    items: tuple[OpResult, ...]
    truncated: int = 0
