"""User-level settings — *choices*, never credentials.

`library.toml` is per-KB and tracked in git; provider and tier preferences are neither. This file
sits beside `~/.klode/registry.toml`, which already established the user-level, untracked location.

**Precedence: argument → environment → file → built-in default.** That chain is only implementable
if an omitted argument is distinguishable from an explicitly-passed one, which is why every
settings-backed CLI flag must default to `None` rather than to its value. `--tier` defaulting to
`"auto"` made `klode ingest x` and `klode ingest x --tier auto` produce identical namespaces, so
the resolver could not tell a deliberate choice from silence. `resolve()` records which source won
for exactly this reason: a split configuration surface nobody can audit is worse than one file.

**What stays out, and why:**

- **API keys** — environment only (`ANTHROPIC_API_KEY`). A key in a file is a key in a backup.

**What was moved IN, and why the earlier reasoning was wrong.** `ingest.docling_url` was env-only
on the grounds that it names an internal host. That conflated *topology* with a *credential*. A
docling-serve URL is not a secret: anyone who can route to the address can use the service, so URL
obscurity protects nothing — the real control is where the service binds (bind it to a private
interface, not to `0.0.0.0`). Meanwhile the env-only rule imposed a genuine cost: the one backend
that fixes multi-column reading order was unreachable unless you remembered to export a variable,
which is how a capability goes unused. So it is configurable here, and the *credential* line is
drawn harder rather than more vaguely — `tests/test_settings.py::SecretsStayOut` asserts no
key/token/secret/password may ever become a setting.

A URL in this file is still topology in a backup. That is a real cost, accepted knowingly: it is
not a credential, it grants nothing on its own, and `KLODE_DOCLING_URL` still overrides it for
anyone who prefers to keep it ephemeral.

**Scope, stated honestly:** this configures a judge *model* and ingest defaults. It is not a
"provider" abstraction — there is exactly one judge transport (Anthropic). Calling it provider
configuration would promise a contract that does not exist.

**The judge settings are not yet consumed.** `klode review` still builds its own judge; these keys
are resolved and displayed but nothing reads them. They are declared here so the resolver and its
diagnostics are testable ahead of that wiring — but until `review` reads them, setting
`judge.model` changes nothing, and this paragraph is the honest statement of that.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Setting", "Settings", "SPEC", "load", "resolve", "settings_path", "DEFAULTS"]

ARG, ENV, FILE, DEFAULT, UNSET = "argument", "environment", "file", "default", "unset"


@dataclass(frozen=True)
class Spec:
    """One setting: where it may come from, and how to read it."""
    section: str
    key: str
    env: str | None
    default: object
    kind: type
    help: str
    choices: tuple = ()          # allowed values, when the domain is closed
    lo: object = None            # inclusive numeric bounds
    hi: object = None
    prefixes: tuple = ()         # allowed string prefixes, when the shape is constrained


SPEC: tuple[Spec, ...] = (
    Spec("judge", "model", "KLODE_JUDGE_MODEL", None, str,
         "model id for the rubric judge — no default on purpose (self-enhancement bias: it must "
         "differ from whatever produced the draft)"),
    Spec("judge", "permutations", "KLODE_JUDGE_PERMUTATIONS", 2, int,
         "how many opposed level orders to average over", lo=1, hi=16),
    Spec("judge", "hurdle", "KLODE_JUDGE_HURDLE", 60, int,
         "Go/Recycle threshold, 0..100", lo=0, hi=100),
    Spec("ingest", "tier", "KLODE_INGEST_TIER", "auto", str,
         "default PDF extraction tier. `marker` is selectable but is NOT in the `auto` ladder — a "
         "backend earns a ladder slot by measuring better (eval/extract_bakeoff.py), not by being "
         "available",
         choices=("auto", "pdftotext", "xberg", "docling", "marker")),
    Spec("ingest", "verify", "KLODE_INGEST_VERIFY", True, bool,
         "check extraction integrity before promoting to the shelf"),
    Spec("ingest", "docling_url", "KLODE_DOCLING_URL", None, str,
         "docling-serve endpoint, e.g. http://<host>:15001 — the remote layout backend. Bind the "
         "service to a private interface; this URL is not a secret and must not be treated as one",
         prefixes=("http://", "https://")),
    Spec("ingest", "marker_url", "KLODE_MARKER_URL", None, str,
         "marker_server endpoint, e.g. http://<host>:15002 — remote-only, there is no local marker",
         prefixes=("http://", "https://")),
    Spec("ingest", "marker_mode", "KLODE_MARKER_MODE", "fast", str,
         "marker conversion mode. `fast` uses a lightweight layout detector and block-OCRs only "
         "garbled content; `balanced` runs the VLM layout model over every page. `fast` REDUCES "
         "VLM use but does not eliminate it — where the VLM inference server cannot start, an "
         "affected document stalls while it fails and then completes by fallback, so the symptom "
         "is latency and not an error",
         choices=("fast", "balanced")),
)

DEFAULTS = {f"{s.section}.{s.key}": s.default for s in SPEC}


@dataclass(frozen=True)
class Setting:
    name: str
    value: object
    source: str          # ARG | ENV | FILE | DEFAULT | UNSET

    def __str__(self) -> str:
        shown = "(unset)" if self.value is None else repr(self.value)
        return f"{self.name:<24} {shown:<24} {self.source}"


class Settings(dict):
    """`name -> Setting`, so the winning value AND its origin are both available."""

    def value(self, name: str):
        s = self.get(name)
        return s.value if s else None

    def source(self, name: str) -> str:
        s = self.get(name)
        return s.source if s else UNSET


def settings_path(explicit: str | Path | None = None, *, home: str | Path | None = None) -> Path:
    base = Path(home) if home is not None else Path.home()
    return Path(explicit).expanduser() if explicit else base / ".klode" / "settings.toml"


def load(explicit: str | Path | None = None, *, home: str | Path | None = None) -> dict:
    """Parse the settings file into `{"section.key": value}`. A missing file is not an error — the
    same posture as the registry. A malformed one IS, because silently ignoring a file the user
    wrote is how a setting appears to have no effect."""
    path = settings_path(explicit, home=home)
    if not path.is_file():
        return {}
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{path}: invalid TOML — {e}") from e
    except OSError as e:
        raise ValueError(f"{path}: cannot read — {e}") from e

    known = {(s.section, s.key) for s in SPEC}
    out: dict = {}
    for section, body in raw.items():
        if not isinstance(body, dict):
            raise ValueError(f"{path}: [{section}] must be a table, got {type(body).__name__}")
        for key, val in body.items():
            if (section, key) not in known:
                # an unknown key is nearly always a typo, and silently dropping it is how a
                # setting "does nothing" with no explanation
                raise ValueError(f"{path}: unknown setting [{section}].{key} — known keys: "
                                 + ", ".join(f"{s.section}.{s.key}" for s in SPEC))
            out[f"{section}.{key}"] = val
    return out


def _from_env(spec: Spec):
    if not spec.env:
        return None, False
    raw = os.environ.get(spec.env)
    if raw is None or raw == "":            # an empty var is absence, not a value
        return None, False
    if spec.kind is bool:
        low = raw.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True, True
        if low in ("0", "false", "no", "off"):
            return False, True
        raise ValueError(f"{spec.env}={raw!r} is not a boolean (use true/false)")
    if spec.kind is int:
        try:
            return int(raw), True
        except ValueError:
            raise ValueError(f"{spec.env}={raw!r} is not an integer")
    return raw, True


def _validate(spec: Spec, value, where: str):
    """Domain validation, applied to EVERY source. Type-checking alone accepted tier="bogus",
    hurdle=999, permutations=0, and an empty model — values that pass as the right type and then
    fail much later, or silently do the wrong thing."""
    if spec.choices and value not in spec.choices:
        raise ValueError(f"{where}: [{spec.section}].{spec.key} must be one of "
                         f"{spec.choices}, got {value!r}")
    if spec.lo is not None and value < spec.lo:
        raise ValueError(f"{where}: [{spec.section}].{spec.key} must be >= {spec.lo}, got {value}")
    if spec.hi is not None and value > spec.hi:
        raise ValueError(f"{where}: [{spec.section}].{spec.key} must be <= {spec.hi}, got {value}")
    if spec.kind is str and not spec.choices and not str(value).strip():
        raise ValueError(f"{where}: [{spec.section}].{spec.key} must not be empty")
    if spec.prefixes and not str(value).lower().startswith(spec.prefixes):
        # caught HERE rather than at the first HTTP call: a typo'd scheme otherwise surfaces as a
        # backend failure during an ingest, blamed on the backend
        raise ValueError(f"{where}: [{spec.section}].{spec.key} must start with one of "
                         f"{spec.prefixes}, got {value!r}")
    return value


def _coerce(spec: Spec, value, where: str):
    if spec.kind is bool and not isinstance(value, bool):
        raise ValueError(f"{where}: [{spec.section}].{spec.key} must be a boolean, "
                         f"got {type(value).__name__}")
    if spec.kind is int and (isinstance(value, bool) or not isinstance(value, int)):
        raise ValueError(f"{where}: [{spec.section}].{spec.key} must be an integer, "
                         f"got {type(value).__name__}")
    if spec.kind is str and not isinstance(value, str):
        raise ValueError(f"{where}: [{spec.section}].{spec.key} must be a string, "
                         f"got {type(value).__name__}")
    return _validate(spec, value, where)


def resolve(args=None, *, file_values: dict | None = None, explicit=None, home=None) -> Settings:
    """Apply the precedence chain and record which source won for every setting.

    `args` is an argparse namespace (or any object with attributes). A settings-backed flag MUST
    default to `None` there, or an omitted flag is indistinguishable from an explicit one and the
    argument level of this chain silently swallows the other three.
    """
    if file_values is None:
        fv = load(explicit, home=home)
    else:
        # An injected mapping must face the SAME unknown-key check as a file on disk, or the
        # documented guarantee ("unknown keys are rejected") holds only for one of two paths.
        known = {f"{s.section}.{s.key}" for s in SPEC}
        unknown = sorted(set(file_values) - known)
        if unknown:
            raise ValueError(f"unknown setting(s) {unknown} — known: {sorted(known)}")
        fv = file_values
    out = Settings()
    for spec in SPEC:
        name = f"{spec.section}.{spec.key}"
        attr = f"{spec.section}_{spec.key}"

        arg = getattr(args, attr, None) if args is not None else None
        if arg is None and args is not None:
            arg = getattr(args, spec.key, None)        # allow a bare flag name too (--tier)
        if arg is not None:
            out[name] = Setting(name, _coerce(spec, arg, "argument"), ARG)
            continue

        env_val, found = _from_env(spec)
        if found:
            out[name] = Setting(name, _validate(spec, env_val, spec.env or "environment"), ENV)
            continue

        if name in fv:
            out[name] = Setting(name, _coerce(spec, fv[name], str(settings_path(explicit, home=home))),
                                FILE)
            continue

        out[name] = Setting(name, spec.default, DEFAULT if spec.default is not None else UNSET)
    return out
