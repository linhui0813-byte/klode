"""klode — a grep-grounded, level-of-zoom knowledge library with a citation-rot linter.

Zero runtime dependencies (Python 3.11+ stdlib only). See SPEC.md for the card format and the two
disciplines that keep the library from rotting.

## Public API — the Loop-A contract

A *consumer* (e.g. a supervising "Loop B" agent) should import from `klode.lib` directly, not from
submodules. This surface is the stable contract; the submodules are internal and may be refactored.
Importing `klode.lib` is cheap and pulls in **no** frontends (`cli`, `mcp_server`) or optional/heavy
dependencies (`entail`, `ingest`, OCR) — those load only when their own entry point is used.

The citation-rot linter is a maintenance tool, not part of the read/verify surface — reach it at
`klode.lib.check.check` (the submodule is preserved) or via the `klode check` CLI; it is deliberately not
re-exported here (the name would shadow the `klode.lib.check` module).
"""
from .config import Config, ConfigError
from .console import ConsultRequest, ConsultResult, consult
from .core import EvidenceContext, EvidenceHit
from .core import Resolution as EvidenceResolution   # the grounding-outcome enum (distinct from query.Resolution)
from .query import (
    Resolution,        # a resolved lookup (outcome + candidates + canonical message)
    Verification,      # verify()'s result
    dimension,         # load a craft dimension (a cross-thinker synthesis)
    diagnose,          # symptom text -> ranked dimensions
    framework,         # load one thinker's framework
    resolve,           # free-text name -> a lens
    search,            # BM25 retrieval over cards
    verify,            # check a quote against its source — the occurrence-only citation primitive
)


def verify_evidence(cfg, card, phrase, *, require_stamp=False, today=None):
    """Structured, freshness- and review-aware grounding — the verifier a supervising gate must use.

    Unlike `verify` (occurrence only), a stale (SOURCE_STALE) or review-expired (REVIEW_EXPIRED /
    REVIEW_DATE_INVALID) source does NOT resolve to FOUND, so it cannot ground a criterion; an
    unstamped source is rejected (SOURCE_UNSTAMPED) only when `require_stamp=True`. Returns an
    `EvidenceHit` whose `.resolution` is an `EvidenceResolution`. `services` is imported lazily so
    plain `import klode.lib` stays cheap."""
    from . import services
    return services.verify_evidence(cfg, card, phrase, require_stamp=require_stamp, today=today)


def verify_context(cfg, card, phrase, *, context_lines=3, max_window=40, require_stamp=False, today=None):
    """The evidence-context op — `verify_evidence` PLUS the bounded surrounding source text a judge
    reads to score a claim. Returns an `EvidenceContext`; `usable` is True only for a resolved, fresh
    anchor with a locatable span. The window is at most `max_window` lines and always contains the
    match (its whole span when that fits, else the match start). Raises ValueError on bad bounds."""
    from . import services
    return services.verify_context(cfg, card, phrase, context_lines=context_lines,
                                   max_window=max_window, require_stamp=require_stamp, today=today)

__version__ = "0.2.1"      # the single source of truth: pyproject reads this; mcp_server derives from it

__all__ = [
    "Config", "ConfigError",
    "consult", "ConsultRequest", "ConsultResult",
    "resolve", "Resolution",
    "verify", "Verification",
    "verify_evidence", "verify_context", "EvidenceHit", "EvidenceContext", "EvidenceResolution",
    "dimension", "framework", "diagnose", "search",
    "__version__",
]
