"""The KB registry — a manifest that maps a KB `id` to its `library.toml`, so a discovery
surface (the `lode kbs` CLI, the `list_kbs` MCP tool) can enumerate the knowledge bases a user
has installed WITHOUT the KBs living inside this engine repo.

Zero dependencies (stdlib `tomllib`/`pathlib`), and internal by design: this module is consumed
by the adapters, never re-exported on the `lode.lib` facade. It never imports an adapter, so the
layering guard holds.

Manifest format — an array of tables, one per KB:

    [[kb]]
    id   = "storycraft"                       # a slug; the registry address for this KB
    path = "~/kbs/storycraft/library.toml"    # relative paths resolve against the manifest's dir

Lookup precedence (first that exists wins):
    1. an explicit path (e.g. the CLI `--registry` flag)
    2. a project-level manifest at `<cwd>/.lode/registry.toml`
    3. a user-level manifest at `~/.lode/registry.toml`
When none exists, the registry is simply empty — that is not an error.

Reconciliation rule (recorded): the manifest `id` is authoritative as the registry address. A
KB's own `Config.id` may differ (a manifest may alias a KB under a different registry id); that is
allowed, not an error. Malformed manifests and duplicate ids ARE hard errors, raised as
`RegistryError` — a `ConfigError` subclass, so the adapters' existing `except ConfigError`
handlers catch it and exit consistently.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .config import Config, ConfigError, _SLUG_RE


class RegistryError(ConfigError):
    """A registry manifest is missing (when explicitly named), malformed, or internally inconsistent."""


@dataclass(frozen=True)
class KB:
    """One registered KB: its registry id and the resolved absolute path to its `library.toml`."""
    id: str
    path: Path


@dataclass(frozen=True)
class KBInfo:
    """The catalog view of one KB — id + human description, plus whether it currently loads.
    Discovery is lazy: a KB whose `library.toml` is broken is reported `ok=False`, not raised,
    so one bad entry never blanks the whole catalog."""
    id: str
    name: str
    description: str
    path: Path
    ok: bool
    error: str | None = None


def _manifest_path(explicit: str | Path | None, *,
                   start: str | Path | None, home: str | Path | None) -> Path | None:
    """Resolve the manifest to read by precedence, or None when no registry exists."""
    if explicit is not None:
        p = Path(explicit).expanduser()      # accept `~/…` like the manifest's own entry paths
        if not p.is_file():
            raise RegistryError(f"registry manifest not found: {p}")
        return p
    base = Path(start) if start is not None else Path.cwd()
    hb = Path(home) if home is not None else Path.home()
    for cand in (base / ".lode" / "registry.toml", hb / ".lode" / "registry.toml"):
        if cand.is_file():
            return cand
    return None


def load(explicit: str | Path | None = None, *,
         start: str | Path | None = None, home: str | Path | None = None) -> tuple[KB, ...]:
    """The registered KBs, sorted by id. Empty when no manifest exists. Fails loud (`RegistryError`)
    on an explicitly-named-but-missing manifest, invalid TOML, a missing `id`/`path`, an id that is
    not a slug, or a duplicate id."""
    path = _manifest_path(explicit, start=start, home=home)
    if path is None:
        return ()
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise RegistryError(f"{path}: invalid TOML — {e}") from e
    except UnicodeDecodeError as e:
        raise RegistryError(f"{path}: not valid UTF-8 — {e}") from e
    except OSError as e:
        raise RegistryError(f"{path}: cannot read — {e}") from e

    entries = raw.get("kb", [])
    if not isinstance(entries, list):
        raise RegistryError(f"{path}: [[kb]] must be an array of tables")
    base = path.parent
    out: list[KB] = []
    seen: set[str] = set()
    for i, e in enumerate(entries, 1):
        if not isinstance(e, dict):
            raise RegistryError(f"{path}: [[kb]] entry #{i} must be a table")
        kid = e.get("id")
        rel = e.get("path")
        if not isinstance(kid, str) or not kid.strip():
            raise RegistryError(f"{path}: [[kb]] entry #{i} is missing a string `id`")
        kid = kid.strip()
        if not _SLUG_RE.fullmatch(kid):
            raise RegistryError(f"{path}: [[kb]] id {kid!r} is not a valid slug "
                                "(lowercase letters, digits, single hyphens)")
        if not isinstance(rel, str) or not rel.strip():
            raise RegistryError(f"{path}: [[kb]] entry {kid!r} is missing a string `path`")
        if kid in seen:
            raise RegistryError(f"{path}: duplicate KB id {kid!r}")
        seen.add(kid)
        # Expand `~` FIRST (expanduser only acts on a LEADING ~), then treat an absolute path as-is
        # and resolve a relative one against the manifest's own directory.
        target = Path(rel.strip()).expanduser()
        target = target if target.is_absolute() else base / target
        out.append(KB(id=kid, path=target.resolve()))
    return tuple(sorted(out, key=lambda k: k.id))


def resolve(kb: KB) -> Config:
    """Load a registered KB's `Config`, failing loud with the id + path when it cannot be loaded."""
    try:
        return Config.load(kb.path)
    except ConfigError as e:
        raise RegistryError(f"KB {kb.id!r} at {kb.path} could not be loaded: {e}") from e


def describe(kb: KB) -> KBInfo:
    """The catalog view of one KB — never raises; a broken KB is reported `ok=False` with its error."""
    try:
        cfg = Config.load(kb.path)
    except ConfigError as e:
        return KBInfo(id=kb.id, name="", description="", path=kb.path, ok=False, error=str(e))
    return KBInfo(id=kb.id, name=cfg.name, description=cfg.description, path=kb.path, ok=True)


def catalog(entries: tuple[KB, ...]) -> list[KBInfo]:
    """Describe every registered KB — the passive list a discovery surface renders (no ranking)."""
    return [describe(kb) for kb in entries]
