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
- **`KLODE_DOCLING_URL`** — environment only. It names an internal host; the reasoning is recorded
  in `formats/pdf.py` and this file does not override it.

**Scope, stated honestly:** this configures a judge *model* and ingest defaults. It is not a
"provider" abstraction — there is exactly one judge transport (Anthropic). Calling it provider
configuration would promise a contract that does not exist.
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


SPEC: tuple[Spec, ...] = (
    Spec("judge", "model", "KLODE_JUDGE_MODEL", None, str,
         "model id for the rubric judge — no default on purpose (self-enhancement bias: it must "
         "differ from whatever produced the draft)"),
    Spec("judge", "permutations", "KLODE_JUDGE_PERMUTATIONS", 2, int,
         "how many opposed level orders to average over"),
    Spec("judge", "hurdle", "KLODE_JUDGE_HURDLE", 60, int,
         "Go/Recycle threshold, 0..100"),
    Spec("ingest", "tier", "KLODE_INGEST_TIER", "auto", str,
         "default PDF extraction tier"),
    Spec("ingest", "verify", "KLODE_INGEST_VERIFY", True, bool,
         "check extraction integrity before promoting to the shelf"),
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
    return value


def resolve(args=None, *, file_values: dict | None = None, explicit=None, home=None) -> Settings:
    """Apply the precedence chain and record which source won for every setting.

    `args` is an argparse namespace (or any object with attributes). A settings-backed flag MUST
    default to `None` there, or an omitted flag is indistinguishable from an explicit one and the
    argument level of this chain silently swallows the other three.
    """
    fv = load(explicit, home=home) if file_values is None else file_values
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
            out[name] = Setting(name, env_val, ENV)
            continue

        if name in fv:
            out[name] = Setting(name, _coerce(spec, fv[name], str(settings_path(explicit, home=home))),
                                FILE)
            continue

        out[name] = Setting(name, spec.default, DEFAULT if spec.default is not None else UNSET)
    return out
