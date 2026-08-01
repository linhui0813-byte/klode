"""KBPool — the multi-KB addressing substrate for the MCP server.

Maps a registry KB `id` to its lazily-loaded, cached `Config`. Built from `registry.load(...)`
for multi-KB serving, or wrapped around a single already-loaded `Config` for legacy `--config`
single-KB mode. Zero dependencies beyond stdlib + `klode.lib` internals; it imports no adapter
(`cli`/`mcp_server`), so the layering guard holds.

A single-entry pool exposes its sole id as `default`; a multi-entry pool has no default (the
caller must name a `kb`). A broken KB surfaces only when addressed — never at construction — so
one bad entry never disables the whole server.
"""
from __future__ import annotations

from . import registry
from .config import Config
from .registry import KB, RegistryError


class KBPool:
    def __init__(self, entries: tuple[KB, ...]):
        self._entries: dict[str, KB] = {kb.id: kb for kb in entries}
        self._cache: dict[str, Config] = {}
        self._default: str | None = next(iter(self._entries)) if len(self._entries) == 1 else None

    def ids(self) -> tuple[str, ...]:
        """Every registered id, sorted — stable addressing and deterministic fan-out order."""
        return tuple(sorted(self._entries))

    @property
    def default(self) -> str | None:
        """The sole id when exactly one KB is registered, else None."""
        return self._default

    def config(self, kb_id: str) -> Config:
        """The addressed KB's `Config`, lazily loaded then memoized. Raises `RegistryError`
        naming the unknown id (and listing every valid id), or naming a registered-but-broken
        KB whose `library.toml` fails to load."""
        if kb_id not in self._entries:
            valid = ", ".join(self.ids()) or "(none registered)"
            raise RegistryError(f"unknown KB {kb_id!r} — registered KBs: {valid}")
        if kb_id not in self._cache:
            self._cache[kb_id] = registry.resolve(self._entries[kb_id])   # broken KB -> RegistryError
        return self._cache[kb_id]

    def catalog(self) -> list[registry.KBInfo]:
        """Describe every KB THIS pool serves (id + self-description), in sorted-id order — the
        passive catalog `list_kbs` renders. Lazy and error-isolated: a broken KB is reported
        `ok=False`, never raised, so one bad entry never blanks the catalog. Because it is sourced
        from the pool, every listed id is guaranteed addressable via `kb`."""
        return [registry.describe(self._entries[kid]) for kid in self.ids()]

    @classmethod
    def from_registry(cls, explicit=None, *, start=None, home=None) -> "KBPool":
        """Build a pool over every KB in the resolved registry manifest."""
        return cls(registry.load(explicit, start=start, home=home))

    @classmethod
    def single(cls, cfg: Config) -> "KBPool":
        """A one-KB pool wrapping an already-loaded `Config` (legacy `--config` mode). Its id is
        `cfg.id`, pre-cached so it is never reloaded, and it is the pool's default."""
        pool = cls((KB(id=cfg.id, path=cfg.config_path),))
        pool._cache[cfg.id] = cfg
        return pool
