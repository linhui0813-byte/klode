"""lodlib — a grep-grounded, level-of-zoom knowledge library with a citation-rot linter.

Zero runtime dependencies (Python 3.11+ stdlib only). See SPEC.md for the card format and the two
disciplines that keep the library from rotting.

## Public API — the Loop-A contract

A *consumer* (e.g. a supervising "Loop B" agent) should import from `lodlib` directly, not from
submodules. This surface is the stable contract; the submodules are internal and may be refactored.
Importing `lodlib` is cheap and pulls in **no** frontends (`cli`, `mcp_server`) or optional/heavy
dependencies (`entail`, `ingest`, OCR) — those load only when their own entry point is used.

The citation-rot linter is a maintenance tool, not part of the read/verify surface — reach it at
`lodlib.check.check` (the submodule is preserved) or via the `lib check` CLI; it is deliberately not
re-exported here (the name would shadow the `lodlib.check` module).
"""
from .config import Config, ConfigError
from .console import ConsultRequest, ConsultResult, consult
from .query import (
    Resolution,        # a resolved lookup (outcome + candidates + canonical message)
    Verification,      # verify()'s result
    dimension,         # load a craft dimension (a cross-thinker synthesis)
    diagnose,          # symptom text -> ranked dimensions
    framework,         # load one thinker's framework
    resolve,           # free-text name -> a lens
    search,            # BM25 retrieval over cards
    verify,            # check a quote against its source — the un-fakeable-citation primitive
)

__version__ = "0.1.0"

__all__ = [
    "Config", "ConfigError",
    "consult", "ConsultRequest", "ConsultResult",
    "resolve", "Resolution",
    "verify", "Verification",
    "dimension", "framework", "diagnose", "search",
    "__version__",
]
