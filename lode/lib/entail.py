"""Optional entailment check — the semantic second opinion the grep guard cannot give.

`lode check` proves an anchor RESOLVES; it cannot prove the quoted span ENTAILS the card's
claim, that qualifications were preserved, or that the source is credible. This module adds
that missing check as an **opt-in, warn-only** layer (`lode check --entail`).

Two disciplines, both load-bearing, both drawn from the 2026 attribution literature:

  1. **Warn, never gate.** The accuracy ceiling for automatic attribution/entailment checking is
     ~77-80% balanced accuracy (AttributionBench; LLM-AggreFact). A metric that hard-fails CI at
     that ceiling trades silent drift for noisy false failures. The grep resolve stays the only
     hard gate; entailment is a second opinion the human reads.
  2. **Window, don't dump.** Whole-document NLI is unreliable; sentence/window-granularity NLI
     recovers accuracy and is far cheaper (SummaC). The grep anchor already localises the span,
     so we score only the tight window around the resolved anchor against the card's claim.

The engine is a small, pinned, greedy-decoded NLI/fact-check model (MiniCheck-Flan-T5-Large,
770M, GPT-4-level fact-check at ~400x lower cost — or AlignScore). It ships behind the optional
`[entail]` extra; the DEFAULT lode path stays zero-dependency. When the extra is absent,
`lode check --entail` degrades to a clear NOTE and changes nothing — grep still gated the build.

This module is import-safe with zero dependencies: the heavy model import happens lazily inside
`load_backend`, never at module load. Everything above the backend (windowing, claim extraction,
thresholding) is pure stdlib and deterministic.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .common import haystacks, occurs, parse_markers, read

# Pinned for reproducible warnings: same model revision + greedy decoding => bitwise-stable
# support scores. Override with `--entail-model`. Encoder/seq2seq NLI models are deterministic
# (argmax, no sampling), which is the only reason an "advisory" score can also be reproducible.
DEFAULT_MODEL = "lytang/MiniCheck-Flan-T5-Large"

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
# strip anchor scaffolding from a claim sentence so the model scores the PROSE, not the marker
_MARKER_SPAN = re.compile(r"\(\s*`?(?:grep|search)(?:-re)?:.*?\)", re.S)
_TICKS = re.compile(r"`[^`]*`")


class EntailUnavailable(RuntimeError):
    """The optional entailment deps are not installed. Carries install guidance."""


@dataclass(frozen=True)
class EntailScore:
    card: str
    claim: str
    phrase: str
    support: float          # P(window entails claim), in [0, 1]
    window: str

    def low(self, threshold: float) -> bool:
        return self.support < threshold


# ---- windowing + claim extraction (pure, deterministic, stdlib) -----------
def sentences(text: str) -> list[str]:
    """Split prose into sentences on `.!?` boundaries — coarse but deterministic, which is all
    the windowing needs."""
    return [s.strip() for s in _SENT_SPLIT.split(text.strip()) if s.strip()]


def window_around(source_raw: str, phrase: str, *, context: int = 1) -> str | None:
    """The tight window (the anchor's sentence ± `context` neighbours) the anchor resolves in —
    the SummaC-granularity premise the NLI model scores. Reuses the SAME tolerant matcher the
    linter uses, at sentence granularity. None when the phrase folds across a sentence boundary
    (rare; the caller falls back to a wider window)."""
    sents = sentences(source_raw)
    for i, s in enumerate(sents):
        if occurs(phrase, haystacks(s)):
            lo, hi = max(0, i - context), min(len(sents), i + context + 1)
            return " ".join(sents[lo:hi]).strip()
    return None


def strip_markers(text: str) -> str:
    """Remove anchor scaffolding — `(grep: …)` spans and stray backtick runs — so a claim
    sentence reads as prose."""
    return _TICKS.sub("", _MARKER_SPAN.sub("", text)).strip()


def claim_for(card_body: str, phrase: str) -> str | None:
    """The card's own sentence carrying this anchor, marker-scaffolding removed — the claim the
    model checks the source window against. Locate the occurrence that sits INSIDE a marker (its
    phrase is delimited by a backtick or quote), not a bare prose mention of the same words —
    otherwise a quote that also appears in earlier prose would score the wrong sentence."""
    idx, start = -1, 0
    while (i := card_body.find(phrase, start)) >= 0:
        if i > 0 and card_body[i - 1] in "`\"":     # a marker delimiter precedes it
            idx = i
            break
        start = i + 1
    if idx < 0:
        idx = card_body.find(phrase)                 # fall back to first occurrence
    if idx < 0:
        return None
    start = max(card_body.rfind(". ", 0, idx), card_body.rfind("\n", 0, idx)) + 1
    m = re.search(r"[.!?](?:\s|$)", card_body[idx:])
    end = idx + m.end() if m else len(card_body)
    claim = strip_markers(card_body[start:end])
    return claim or None


# ---- the backend (lazy, optional) -----------------------------------------
class EntailBackend:
    """Duck-typed interface: `.name` and `.support(premise, claim) -> float in [0,1]`.
    A concrete backend is built by `load_backend`; tests inject a fake."""


def load_backend(model: str = DEFAULT_MODEL) -> EntailBackend:
    """Build the pinned NLI/fact-check backend, importing the heavy deps LAZILY. Raises
    `EntailUnavailable` with install guidance when the `[entail]` extra is absent — the caller
    turns that into a NOTE, never a failure."""
    try:
        from minicheck.minicheck import MiniCheck   # type: ignore  # optional dep
    except ImportError as e:
        raise EntailUnavailable(
            "entailment checking needs the optional extra — the default path stays zero-dep:\n"
            "    pip install 'lode[entail]'\n"
            f"(missing: {getattr(e, 'name', e)})") from e

    short = model.rsplit("/", 1)[-1]

    class _MiniCheckBackend(EntailBackend):
        name = model

        def __init__(self) -> None:
            # MiniCheck picks its weights by short name (e.g. 'flan-t5-large'); greedy/argmax,
            # no sampling => the returned probability is reproducible for a fixed revision.
            self._scorer = MiniCheck(model_name=short.lower().replace("minicheck-", ""))

        def support(self, premise: str, claim: str) -> float:
            _pred, probs, _raw, _ = self._scorer.score(docs=[premise], claims=[claim])
            return float(probs[0])

    # Model init can fail at runtime (weight download, bad model name, API drift). Convert that
    # to EntailUnavailable too, so `--entail` always degrades to a NOTE — never a crash.
    try:
        return _MiniCheckBackend()
    except EntailUnavailable:
        raise
    except Exception as e:
        raise EntailUnavailable(f"could not initialise the entailment model {model!r}: {e}") from e


# ---- scoring a whole card -------------------------------------------------
def score_card(card_text: str, source_raw: str, backend: EntailBackend, card_id: str) -> list[EntailScore]:
    """Every literal anchor in a card, scored: window (premise) vs claim. Regex anchors are
    skipped — a regex spans a gap, so there is no single verbatim claim to entail."""
    out: list[EntailScore] = []
    body = card_text
    for mk in parse_markers(card_text):
        if mk.regex:
            continue
        window = window_around(source_raw, mk.phrase)
        if window is None:
            # the phrase folds across a sentence boundary — no clean premise. Skip rather than
            # score the claim against an arbitrary head slice, which would be a misleading verdict.
            continue
        claim = claim_for(body, mk.phrase)
        if not claim:
            continue
        out.append(EntailScore(card=card_id, claim=claim, phrase=mk.phrase,
                               support=backend.support(window, claim), window=window))
    return out
