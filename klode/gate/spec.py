"""CriterionSpec v1 — the gate's SOLE input: an authored, versioned, human-approved rubric.

## Why this exists

The walking skeleton promoted a synthesis's bold Craft bullets into criteria: ids were positional
(`C1`, `C2` — reordering the prose renamed the rubric), the `statement` was an *imperative summary*
of the source rather than the source, and nothing recorded which parts were the author's words and
which were the operator's inference. The anchor still resolved, so the whole thing looked
quote-grade. It was not: this grounds perfectly clean —

    statement: "Keep every redundant clause; never trim."
    anchor:    "Trim every clause the reader can infer"

because `ground()` proves the *anchor* resolves, never that the *statement* follows from it.

A CriterionSpec closes that by refusing to let one `source:` pointer cover a mixed bag of claims.
Every authored text field carries its own **epistemic envelope**, and each `kind` is mechanically
enforced — an `explicit` field must resolve VERBATIM (the value *is* the quote, so statement and
evidence cannot diverge), and anything inferred must show its warrant:

| kind | what it means | enforced |
|---|---|---|
| `explicit` | the source's own words | `value` itself occurs verbatim in a raw source line |
| `paraphrase` | the source's claim, restated | grounding evidence **and** a warrant |
| `derived` | follows from the source, not stated by it | a warrant |
| `operator_policy` | the operator's instrument, not the book's | a warrant |
| `unknown` | the author did not state this | `value` MUST be null |

`unknown` is the point of the table. A schema slot with no `unknown` is a manufacturing order: a
required `exceptions:` field makes the model invent exceptions for a principle whose author stated
none. Here, "not stated" is a first-class, checkable answer.

**What `explicit` guarantees, exactly.** The value must occur in a RAW source line — resolution
`FOUND`, never `FOLDED_ONLY`. That distinction is load-bearing: the grep normalizer folds
hyphenation, so under a folded match the source `Writers must re-sign` would satisfy the explicit
value `Writers must resign`, which asserts the opposite. Requiring a raw-line occurrence means a
quote cannot span a line break — shorten the quote, or mark it `paraphrase`.

**What it does NOT guarantee.** Only that a quotation is genuine. A criterion that contradicts its
source can still be authored as `paraphrase` with any non-empty warrant, because warrant *presence*
is checked, not entailment. That is what human approval is for, and why approval is a gate.

## The admission gate

`admission: candidate` loads but CANNOT be reviewed against. Only `human_approved` reaches the gate.
This is klode's governing rule in code: agents generate candidates; only a human promotes them.

`approved_digest` binds the approval to the rubric body, so an edit after approval is detected
rather than inherited. Be precise about what that is worth: the digest is **unkeyed and stored
beside the thing it signs**, so it catches a careless or automated edit that does not think to
reseal — it does not resist an agent that recomputes it, and it is not a signature. The human half
of "human_approved" is a workflow guarantee you enforce outside this file (review, code owners,
a protected branch); nothing inside a JSON document can establish it.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from klode import lib   # the public facade — this package never reaches into klode internals

from .criteria import Criterion

SCHEMA = "klode.criterion-spec/v1"

KINDS = ("explicit", "paraphrase", "derived", "operator_policy", "unknown")
_NEEDS_WARRANT = ("paraphrase", "derived", "operator_policy")
_NEEDS_EVIDENCE = ("explicit", "paraphrase")
_ADMISSIONS = ("candidate", "human_approved")
_GROUNDED = (lib.EvidenceResolution.FOUND, lib.EvidenceResolution.FOLDED_ONLY)

# A stable id: visible, no separators or dots-only-numbers, not purely numeric. `^C\d+$` alone
# rejected one shape and let `c1`, `1`, `criterion-1` through — all just as positional.
_ID_RE = re.compile(r"^(?!\d+$)[A-Za-z][A-Za-z0-9._-]*$")
_POSITIONAL_ID = re.compile(r"^[A-Za-z]\d+[a-z]?$")     # C1, c1, C1a — a slot number wearing a letter
_DIMENSION_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")  # also the path component, so: no dots, no seps
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_SPEC_BYTES = 4 * 1024 * 1024                         # a rubric is prose, not a dataset


class SpecError(ValueError):
    """A spec that must not be used. Every validation failure is fatal — a rubric that cannot be
    trusted is not downgraded to a warning, because the gate's whole value is refusing to score
    against evidence it cannot stand behind."""


# --------------------------------------------------------------------------- typed accessors
# Hand-edited JSON is untrusted input. Every accessor raises SpecError, never TypeError/
# AttributeError: `load` promises a clean SpecError boundary, and an `or`-default silently ERASES a
# supplied wrong type (`evidence: ""` became `[]`, `fields: []` became `{}`) instead of rejecting it.


def _req(d: dict, key: str, where: str):
    if key not in d:
        raise SpecError(f"{where}: missing required key {key!r}")
    return d[key]


def _as_obj(v, where: str) -> dict:
    if not isinstance(v, dict):
        raise SpecError(f"{where}: must be an object, got {type(v).__name__}")
    return v


def _as_list(v, where: str) -> list:
    if not isinstance(v, list):
        raise SpecError(f"{where}: must be a list, got {type(v).__name__}")
    return v


def _as_int(v, where: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):   # bool is an int subclass; `nth: true` is not 1
        raise SpecError(f"{where}: must be an integer, got {type(v).__name__}")
    return v


def _visible(s: str) -> str:
    """Text with all format/control characters removed. `strip()` leaves U+200B, so a zero-width
    space passed every 'non-empty' check — a level descriptor could validate while being blank."""
    return "".join(c for c in s if unicodedata.category(c) not in ("Cf", "Cc", "Zs", "Zl", "Zp")).strip()


def _as_text(v, where: str, *, required: bool = True) -> str:
    if not isinstance(v, str):
        raise SpecError(f"{where}: must be a string, got {type(v).__name__}")
    try:
        v.encode("utf-8")          # a lone surrogate ("\ud800") survives JSON but not encoding,
    except UnicodeEncodeError:     # and escaped SpecError later, inside content_digest
        raise SpecError(f"{where}: contains unpaired surrogate characters and is not encodable text")
    if required and not _visible(v):
        raise SpecError(f"{where}: must contain visible text (got blank or format characters only)")
    return v.strip()


# --------------------------------------------------------------------------- model


@dataclass(frozen=True)
class Field:
    """One authored text value plus the epistemic envelope that says what kind of claim it is."""
    value: str | None
    kind: str
    warrant: str = ""
    evidence: tuple[lib.Marker, ...] = ()
    cards: tuple[str, ...] = ()          # per-evidence card id, parallel to `evidence`

    @property
    def stated(self) -> bool:
        return self.kind != "unknown"


@dataclass(frozen=True)
class Level:
    score: int
    descriptor: Field


@dataclass(frozen=True)
class SpecCriterion:
    id: str
    statement: Field
    guidance: Field
    levels: tuple[Level, ...]
    evidence: tuple[lib.Marker, ...]
    cards: tuple[str, ...]               # parallel to `evidence`
    criticality: str = "required"
    fields: "MappingProxyType" = field(default_factory=lambda: MappingProxyType({}))

    @property
    def max_score(self) -> int:
        return self.levels[-1].score

    def to_criterion(self) -> Criterion:
        """The grounding-engine view. Ids are the spec's STABLE ids, so a rubric keeps its identity
        across edits — `C1` was a position, not a name, and labels collected against it were void
        the moment a bullet moved."""
        # `statement.value` is non-empty by construction (parse rejects an 'unknown' statement), so
        # this uses it directly rather than falling back to the id and masking a bad hand-built object
        return Criterion(self.id, self.statement.value,
                         tuple(m.phrase for m in self.evidence),
                         guidance=self.guidance.value or "",
                         criticality=self.criticality, markers=self.evidence)

    def bindings(self) -> tuple[tuple, ...]:
        """`(marker, card)` pairs — the anchor together with the card it was DECLARED against.
        `to_criterion()` cannot carry these, and grounding without them lets a citation silently
        migrate to a different panel card instead of failing."""
        return tuple(zip(self.evidence, self.cards))


@dataclass(frozen=True)
class CriterionSpec:
    dimension: str
    panel: tuple[str, ...]
    criteria: tuple[SpecCriterion, ...]
    admission: str
    fingerprint: "MappingProxyType"
    path: Path | None = None

    @property
    def approved(self) -> bool:
        return self.admission == "human_approved"


# --------------------------------------------------------------------------- approval digest


def _freeze(v):
    """Recursively immutable view. Freezing only the top level left `fingerprint["sources"][card]`
    writable, so a "validated" spec could still be edited in place after parsing."""
    if isinstance(v, dict):
        return MappingProxyType({k: _freeze(x) for k, x in v.items()})
    if isinstance(v, list):
        return tuple(_freeze(x) for x in v)
    return v


def canonical_digest(obj) -> str:
    """sha256 over canonical JSON. Concatenating free prose with separators collides — statement
    `a:b` + guidance `c` serializes identically to statement `a` + guidance `b:c`, and `None`
    collides with the literal string "None". JSON quotes and escapes, so it cannot."""
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def rubric_identity(spec: "CriterionSpec") -> str:
    """Everything that makes two rubrics different to whoever applies them — human rater or model
    judge: not just ids and scales, but the words they are asked to apply and the evidence behind
    them, INCLUDING selectors.

    This is what a calibration is calibrated AGAINST. Reword a level descriptor and you have a
    different instrument, so an agreement number measured on the old one no longer transfers."""
    def marker(m, card):
        return {"card": card, "phrase": m.phrase, "before": m.before, "after": m.after, "nth": m.nth}

    def fld(f):
        return {"value": f.value, "kind": f.kind, "warrant": f.warrant,
                "evidence": [marker(m, c) for m, c in zip(f.evidence, f.cards)]}

    return canonical_digest({
        "dimension": spec.dimension,
        "panel": list(spec.panel),
        "criteria": [{
            "id": c.id, "max_score": c.max_score,
            "statement": fld(c.statement), "guidance": fld(c.guidance),
            "fields": {k: fld(v) for k, v in sorted(c.fields.items())},
            "levels": [{"score": l.score, "descriptor": fld(l.descriptor)} for l in c.levels],
            "evidence": [marker(m, c2) for m, c2 in zip(c.evidence, c.cards)],
        } for c in spec.criteria],
    })


def content_digest(doc: dict) -> str:
    """sha256 over the rubric body — everything that defines what was approved, with the approval
    fields themselves excluded. Canonical (sorted keys, no insignificant whitespace) so formatting
    a file does not invalidate its approval, while any change to a statement, warrant, level,
    citation, panel, or pin does."""
    body = {k: v for k, v in doc.items() if k not in ("admission", "approved_digest")}
    # `surrogatepass`: a lone surrogate is rejected by `_as_text` during parse, but the digest is
    # also computed by `authoring.build` BEFORE parse runs, and a hash function that raises on its
    # input is a crash where a diagnosis belongs. Hash whatever it is; let parse do the rejecting.
    try:
        blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as e:
        # mixed key types make sort_keys raise; a cyclic/non-JSON value makes dumps raise. A hash
        # function that throws on its input is a crash where a diagnosis belongs.
        raise SpecError(f"rubric cannot be canonicalized for its approval digest — {e}") from e
    return hashlib.sha256(blob.encode("utf-8", errors="surrogatepass")).hexdigest()


# --------------------------------------------------------------------------- parsing


def _parse_evidence(raw, where: str) -> tuple[tuple[lib.Marker, ...], tuple[str, ...]]:
    markers, cards = [], []
    for i, e in enumerate(_as_list(raw, f"{where}.evidence")):
        at = f"{where}.evidence[{i}]"
        e = _as_obj(e, at)
        card = _as_text(_req(e, "card", at), f"{at}.card")
        phrase = _as_text(_req(e, "phrase", at), f"{at}.phrase")
        if "regex" in e:
            # A regex anchor trades an exact quote for a pattern, and a canonical rubric is the last
            # place to want that latitude — `.+` is technically an anchor. Quote the source instead.
            # Keyed on PRESENCE: `regex: 0` is not permission to carry the key.
            raise SpecError(f"{at}: regex evidence is not allowed in a canonical criterion — "
                            "cite the source's exact words")
        before = None if e.get("before") is None else _as_text(e["before"], f"{at}.before")
        after = None if e.get("after") is None else _as_text(e["after"], f"{at}.after")
        nth = None
        if e.get("nth") is not None:
            nth = _as_int(e["nth"], f"{at}.nth")
            if nth < 1:
                raise SpecError(f"{at}.nth: must be >= 1 (occurrences are 1-indexed), got {nth}")
        markers.append(lib.Marker(phrase, before=before, after=after, nth=nth))
        cards.append(card)
    return tuple(markers), tuple(cards)


def _parse_field(raw, where: str) -> Field:
    raw = _as_obj(raw, where)
    kind = _req(raw, "kind", where)
    if kind not in KINDS:
        raise SpecError(f"{where}: kind must be one of {KINDS}, got {kind!r}")
    has_warrant, has_evidence = "warrant" in raw, "evidence" in raw

    if kind == "unknown":
        # The whole reason `unknown` exists: "the author did not state this" must be sayable without
        # inventing a value. A value here means the envelope is lying about its own content.
        if raw.get("value") is not None:
            raise SpecError(f"{where}: kind 'unknown' means the source does not state it — "
                            f"`value` must be null, got {raw['value']!r}")
        if has_warrant or has_evidence:
            raise SpecError(f"{where}: kind 'unknown' takes no warrant and no evidence")
        return Field(None, kind)

    value = _as_text(_req(raw, "value", where), f"{where}.value")
    warrant = _as_text(raw["warrant"], f"{where}.warrant") if has_warrant else ""
    if kind in _NEEDS_WARRANT and not warrant:
        raise SpecError(f"{where}: kind {kind!r} is inference, not quotation — it requires a "
                        "`warrant` saying how it follows from the cited source")
    if kind == "explicit" and warrant:
        raise SpecError(f"{where}: kind 'explicit' is the source's own words — it takes no warrant")

    markers, cards = _parse_evidence(raw["evidence"], where) if has_evidence else ((), ())
    if kind in _NEEDS_EVIDENCE and not markers:
        raise SpecError(f"{where}: kind {kind!r} must cite the source it comes from (`evidence`)")
    return Field(value, kind, warrant, markers, cards)


def _parse_levels(raw, where: str) -> tuple[Level, ...]:
    raw = _as_list(raw, f"{where}.levels")
    if len(raw) < 2:
        raise SpecError(f"{where}: `levels` must list at least 2 behaviorally anchored scores — "
                        "a scale with no descriptors is the arbitrary 0-10 this artifact replaces")
    levels = []
    for i, lv in enumerate(raw):
        at = f"{where}.levels[{i}]"
        lv = _as_obj(lv, at)
        score = _as_int(_req(lv, "score", at), f"{at}.score")
        if score != i:
            raise SpecError(f"{at}: scores must run 0..N contiguously — expected {i}, got {score}")
        desc = _parse_field(_req(lv, "descriptor", at), f"{at}.descriptor")
        if not desc.stated:
            raise SpecError(f"{at}: a level descriptor cannot be 'unknown' — an unlabelled level "
                            "is exactly the ambiguity behavioral anchoring exists to remove")
        levels.append(Level(score, desc))
    return tuple(levels)


def _parse_criterion(raw, where: str, seen: set) -> SpecCriterion:
    raw = _as_obj(raw, where)
    cid = _as_text(_req(raw, "id", where), f"{where}.id")
    if _POSITIONAL_ID.match(cid) or not _ID_RE.match(cid):
        raise SpecError(f"{where}: id {cid!r} is not a stable name — it must start with a letter, "
                        "contain only letters/digits/._- (no separators, spaces, or controls), not "
                        "be purely numeric, and not be a bare slot number like 'C1'. A positional "
                        "id voids every human label collected against it as soon as order changes")
    if cid in seen:
        raise SpecError(f"{where}: duplicate criterion id {cid!r}")
    seen.add(cid)

    at = f"criterion {cid!r}"
    statement = _parse_field(_req(raw, "statement", at), f"{at}.statement")
    if not statement.stated:
        raise SpecError(f"{at}: `statement` cannot be 'unknown' — a criterion with no claim is not one")
    guidance = _parse_field(raw.get("guidance", {"value": None, "kind": "unknown"}), f"{at}.guidance")
    levels = _parse_levels(_req(raw, "levels", at), at)
    markers, cards = _parse_evidence(_req(raw, "evidence", at), at)
    if not markers:
        raise SpecError(f"{at}: `evidence` must cite at least one source phrase")
    criticality = raw.get("criticality", "required")
    if criticality == "advisory":
        # Parsed and carried, but the verdict weights and gates it exactly like `required`. Accepting
        # the word while ignoring its meaning misleads the author into thinking a criterion is
        # optional when it can still block a Go.
        raise SpecError(f"{at}: criticality 'advisory' is not implemented — the gate weights and "
                        "gates every criterion identically, so an advisory label would be a lie. "
                        "Use 'required', or leave the criterion out of the rubric")
    if criticality != "required":
        raise SpecError(f"{at}: criticality must be 'required', got {criticality!r}")

    extra_raw = raw.get("fields")
    extra = {}
    if extra_raw is not None:
        for k, v in _as_obj(extra_raw, f"{at}.fields").items():
            if not isinstance(k, str) or not _visible(k):
                raise SpecError(f"{at}.fields: field names must be non-empty strings, got {k!r}")
            extra[k] = _parse_field(v, f"{at}.fields.{k}")
    return SpecCriterion(cid, statement, guidance, levels, markers, cards,
                         criticality, MappingProxyType(extra))


def _all_fields(c: SpecCriterion):
    yield c.statement
    yield c.guidance
    for lv in c.levels:
        yield lv.descriptor
    yield from c.fields.values()


def parse(doc: dict, *, path: Path | None = None) -> CriterionSpec:
    """Structure + envelope validation. Does NOT touch sources — see `validate` for the checks that
    need the corpus (verbatim `explicit` values, fingerprint freshness)."""
    doc = _as_obj(doc, "spec")
    if doc.get("schema") != SCHEMA:
        raise SpecError(f"unsupported schema {doc.get('schema')!r} — expected {SCHEMA!r}")
    dimension = _as_text(_req(doc, "dimension", "spec"), "spec.dimension")
    if not _DIMENSION_RE.match(dimension):
        # This value becomes a path component; a separator or `..` would escape the criteria dir.
        raise SpecError(f"spec.dimension {dimension!r}: must start with a letter and contain only "
                        "letters, digits, '_' or '-' (it is used as a file name)")

    panel_raw = _as_list(_req(doc, "panel", "spec"), "spec.panel")
    if not panel_raw:
        raise SpecError("spec: `panel` must name at least one card")
    panel: list[str] = []
    for i, c in enumerate(panel_raw):
        cid = _as_text(c, f"spec.panel[{i}]")
        if cid in panel:
            # a repeated card is checked twice at grounding and reports `ambiguous-panel`, which
            # would make an otherwise valid rubric permanently Unavailable
            raise SpecError(f"spec.panel: duplicate card {cid!r}")
        panel.append(cid)

    admission = doc.get("admission", "candidate")
    if admission not in _ADMISSIONS:
        raise SpecError(f"spec: admission must be one of {_ADMISSIONS}, got {admission!r}")

    raw_crit = _as_list(_req(doc, "criteria", "spec"), "spec.criteria")
    if not raw_crit:
        raise SpecError("spec: `criteria` must not be empty")
    seen: set = set()
    criteria = tuple(_parse_criterion(c, f"spec.criteria[{i}]", seen) for i, c in enumerate(raw_crit))

    panel_set = set(panel)
    for c in criteria:                              # every citation must name a PANEL card
        cited = set(c.cards) | {x for f in _all_fields(c) for x in f.cards}
        for cardname in sorted(cited):
            if cardname not in panel_set:
                raise SpecError(f"criterion {c.id!r}: cites card {cardname!r}, which is not in the panel "
                                f"{sorted(panel_set)} — provenance must stay inside the declared panel")

    fingerprint = _as_obj(doc["fingerprint"], "spec.fingerprint") if "fingerprint" in doc else {}
    sources = (_as_obj(fingerprint["sources"], "spec.fingerprint.sources")
               if "sources" in fingerprint else {})
    for card, digest in sources.items():
        if not isinstance(digest, str) or not _SHA256_RE.match(digest):
            raise SpecError(f"spec.fingerprint.sources[{card!r}]: must be a 64-character lowercase "
                            f"sha256 hex digest, got {digest!r}")

    if admission == "human_approved":
        # Bind the approval to what was approved. Without this, a rubric can be approved and then
        # edited — the sign-off stays, the content does not.
        stated = doc.get("approved_digest")
        if stated is None:
            raise SpecError("spec: admission 'human_approved' requires an `approved_digest` binding "
                            "the approval to the rubric body — run `python3 -m klode.gate approve`")
        if not isinstance(stated, str) or not _SHA256_RE.match(stated):
            raise SpecError(f"spec.approved_digest: must be a 64-character lowercase sha256 hex "
                            f"digest, got {stated!r}")
        actual = content_digest(doc)
        if stated != actual:
            raise SpecError("spec: the rubric has been EDITED since it was approved "
                            f"(approved {stated[:12]}…, now {actual[:12]}…) — re-read the change, "
                            "then re-approve with `python3 -m klode.gate -c <library.toml> "
                            f"approve {doc.get('dimension', '<dimension>')}`")

    return CriterionSpec(dimension, tuple(panel), criteria, admission,
                         _freeze(fingerprint), path)


# --------------------------------------------------------------------------- corpus validation


def validate(cfg, spec: CriterionSpec, *, require_stamp: bool = True, today=None) -> None:
    """The checks that need the corpus. Raises `SpecError` on the first failure.

    The load-bearing one is `explicit`: the field's OWN value must resolve, and must resolve in a RAW
    source line (`FOUND`, not `FOLDED_ONLY`). That is what makes a quote-grade claim un-fakeable at
    the field level rather than at the object level — the statement/anchor divergence that motivated
    this artifact cannot be expressed, and a hyphenation fold cannot turn `re-sign` into `resign`."""
    check_fingerprint(cfg, spec)
    # A statement's evidence usually repeats the criterion's, and each check re-reads the whole
    # source; dedupe so a rubric validates in one pass over each distinct citation.
    seen: set = set()
    for c in spec.criteria:
        for marker, card in c.bindings():
            _must_ground(cfg, card, marker, f"criterion {c.id!r}", require_stamp, today, seen)
        for f in _all_fields(c):
            for marker, card in zip(f.evidence, f.cards):
                _must_ground(cfg, card, marker, f"criterion {c.id!r} field", require_stamp, today, seen)
            if f.kind == "explicit":
                _must_quote(cfg, f, f"criterion {c.id!r} explicit field", require_stamp, today)


def check_fingerprint(cfg, spec: CriterionSpec) -> None:
    """Every panel card is pinned, installed, and unchanged since the rubric was authored."""
    stored = spec.fingerprint.get("sources") or {}
    for card in spec.panel:
        digest = lib.source_digest(cfg, card)
        if digest is None:
            raise SpecError(f"panel card {card!r}: source is not installed — the rubric cannot be "
                            "validated against a corpus that is not here")
        pinned = stored.get(card)
        if pinned is None:
            raise SpecError(f"panel card {card!r}: absent from `fingerprint.sources` — a rubric that "
                            "does not pin its corpus cannot say whether it has drifted")
        if pinned != digest:
            raise SpecError(f"panel card {card!r}: source has changed since this rubric was authored "
                            f"(pinned {pinned[:12]}…, now {digest[:12]}…) — re-review and re-pin")


def _must_ground(cfg, card, marker, where, require_stamp, today, seen=None) -> None:
    key = (card, marker.phrase, marker.before, marker.after, marker.nth, marker.regex)
    if seen is not None:
        if key in seen:
            return
        seen.add(key)
    ev = lib.verify_evidence(cfg, card, marker, require_stamp=require_stamp, today=today)
    if ev.resolution not in _GROUNDED:
        raise SpecError(f"{where}: {marker.phrase!r} does not ground in {card!r} ({ev.resolution.value})")


def _must_quote(cfg, f: Field, where, require_stamp, today) -> None:
    """An `explicit` value must occur VERBATIM in a raw line of one of its OWN cited cards.

    Checking only `cards[0]` rejected a valid value cited second; checking the whole panel would let
    the quote come from a card the field never cited. Its own evidence cards are the right scope —
    and EVERY one of them is tried before failing, since raising on the first folded card would
    reject a field whose exact quote lives in the second."""
    folded = []
    for card in f.cards:
        ev = lib.verify_evidence(cfg, card, lib.Marker(f.value),
                                 require_stamp=require_stamp, today=today)
        if ev.resolution == lib.EvidenceResolution.FOUND:
            return
        if ev.resolution == lib.EvidenceResolution.FOLDED_ONLY:
            folded.append(card)
    if folded:
        raise SpecError(
            f"{where}: the field's own text {f.value!r} matches {folded!r} only after normalization "
            "(whitespace/hyphenation folding), not verbatim in a source line, and no other cited "
            "card contains it exactly. A fold can invert meaning — 're-sign' folds to 'resign'. "
            "Quote a single line exactly, or mark the field 'paraphrase' with a warrant")
    raise SpecError(f"{where}: the field's own text {f.value!r} does not occur in any of its cited "
                    f"cards {list(f.cards)} — an 'explicit' value IS the quote")


# --------------------------------------------------------------------------- loading


def spec_path(cfg, dimension: str) -> Path | None:
    """Resolve a rubric path, refusing anything that escapes the criteria directory.

    `dimension` reaches here from CLI args and from MCP/CLI service params, so it is untrusted:
    unchecked, `../../elsewhere` reads and (via the authoring tool) WRITES outside the KB."""
    base = getattr(cfg, "criteria", None)
    if not base:
        return None
    if not _DIMENSION_RE.match(dimension or ""):
        raise SpecError(f"dimension {dimension!r}: must start with a letter and contain only "
                        "letters, digits, '_' or '-' — it names a file inside the criteria directory")
    base = Path(base).resolve()
    p = (base / f"{dimension}.json").resolve()
    if not p.is_relative_to(base):                    # belt and braces behind the grammar check
        raise SpecError(f"dimension {dimension!r}: resolves outside {base}")
    return p if p.is_file() else None


def load(cfg, dimension: str, *, require_stamp: bool = True, today=None,
         corpus: bool = True) -> CriterionSpec:
    """Load + validate the dimension's rubric. Raises `SpecError` when absent or invalid — never
    returns a partial rubric, because a gate scoring a reduced rubric can turn Recycle into Go,
    which is the same fail-open the verdict logic already refuses."""
    p = spec_path(cfg, dimension)
    if p is None:
        where = getattr(cfg, "criteria", None) or "<no [frameworks].criteria dir configured>"
        raise SpecError(f"no CriterionSpec for dimension {dimension!r} in {where} — the gate requires "
                        "an authored, human-approved rubric; derive a candidate with "
                        "`python3 -m klode.gate derive`, then fill and approve it")
    try:
        size = p.stat().st_size
        if size > MAX_SPEC_BYTES:
            raise SpecError(f"{p}: {size} bytes exceeds the {MAX_SPEC_BYTES}-byte rubric limit")
        raw = p.read_text(encoding="utf-8")
    except OSError as e:
        raise SpecError(f"{p}: cannot be read — {e}") from e
    except UnicodeDecodeError as e:
        raise SpecError(f"{p}: is not valid UTF-8 — {e}") from e
    try:
        doc = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
    except SpecError:
        raise                                    # the duplicate-key hook, already diagnosed
    except ValueError as e:
        # JSONDecodeError subclasses ValueError, but so does the int digit-conversion limit that a
        # 5000-digit number trips — catching only the subclass let that one escape
        raise SpecError(f"{p}: invalid JSON — {e}") from e
    except RecursionError as e:
        raise SpecError(f"{p}: JSON is nested too deeply to parse safely") from e
    spec = parse(doc, path=p)
    if spec.dimension != dimension:
        raise SpecError(f"{p}: declares dimension {spec.dimension!r} but is filed as {dimension!r}")
    if corpus:
        validate(cfg, spec, require_stamp=require_stamp, today=today)
    return spec


def _no_duplicate_keys(pairs):
    """Reject duplicate JSON keys. `json.loads` keeps the LAST one silently, so
    `{"admission":"candidate","admission":"human_approved"}` parses as approved while a reviewer
    reading top-down sees a candidate."""
    out = {}
    for k, v in pairs:
        if k in out:
            raise SpecError(f"duplicate key {k!r} — a rubric must read the same way to a human and "
                            "to the parser")
        out[k] = v
    return out
