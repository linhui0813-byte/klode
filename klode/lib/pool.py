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

from pathlib import Path

from . import registry
from .config import Config
from .registry import KB, RegistryError


class KBPool:
    def __init__(self, entries: tuple[KB, ...], *, manifest: "Path | None" = None):
        self._manifest = Path(manifest).expanduser() if manifest else None
        self._stamp = self._manifest_stamp()
        self._set_entries(entries)

    def _set_entries(self, entries: tuple[KB, ...]) -> None:
        self._entries: dict[str, KB] = {kb.id: kb for kb in entries}
        self._cache: dict[str, Config] = {}
        self._default: str | None = next(iter(self._entries)) if len(self._entries) == 1 else None

    def _manifest_stamp(self):
        """(mtime_ns, size) of the manifest, or None when there is no file to watch."""
        if self._manifest is None:
            return None
        try:
            st = self._manifest.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _refresh(self) -> None:
        """Re-read the manifest when it has changed on disk.

        A long-running server (the MCP case) built its entries once at startup, so editing the
        registry — moving a KB, adding one — left it serving paths that no longer exist until
        someone restarted it. The manifest's own contract is that "the catalog matches what is
        served", which a frozen snapshot cannot honour. Stat-gated, so the common path is one
        `stat` per call, and re-read only when mtime or size actually moved.

        A manifest that fails to reload (a half-written file mid-edit, a syntax error) keeps the
        PREVIOUS entries and warns: blanking a working catalog because someone is typing is a
        worse failure than briefly serving the last good one. The stamp is advanced either way, so
        a broken manifest warns once rather than on every call.
        """
        if self._manifest is None:
            return
        stamp = self._manifest_stamp()
        if stamp == self._stamp:
            return
        self._stamp = stamp
        try:
            self._set_entries(registry.load(self._manifest))
        except RegistryError as e:
            import sys
            print(f"klode: registry reload failed, keeping the previous catalog — {e}",
                  file=sys.stderr)

    def ids(self) -> tuple[str, ...]:
        """Every registered id, sorted — stable addressing and deterministic fan-out order."""
        self._refresh()
        return tuple(sorted(self._entries))

    @property
    def default(self) -> str | None:
        """The sole id when exactly one KB is registered, else None."""
        self._refresh()
        return self._default

    def config(self, kb_id: str) -> Config:
        """The addressed KB's `Config`, lazily loaded then memoized. Raises `RegistryError`
        naming the unknown id (and listing every valid id), or naming a registered-but-broken
        KB whose `library.toml` fails to load."""
        self._refresh()
        if kb_id not in self._entries:
            valid = ", ".join(sorted(self._entries)) or "(none registered)"
            raise RegistryError(f"unknown KB {kb_id!r} — registered KBs: {valid}")
        if kb_id not in self._cache:
            self._cache[kb_id] = registry.resolve(self._entries[kb_id])   # broken KB -> RegistryError
        return self._cache[kb_id]

    def catalog(self) -> list[registry.KBInfo]:
        """Describe every KB THIS pool serves (id + self-description), in sorted-id order — the
        passive catalog `list_kbs` renders. Lazy and error-isolated: a broken KB is reported
        `ok=False`, never raised, so one bad entry never blanks the catalog. Because it is sourced
        from the pool, every listed id is guaranteed addressable via `kb`."""
        self._refresh()
        return [registry.describe(self._entries[kid]) for kid in sorted(self._entries)]

    @classmethod
    def from_registry(cls, explicit=None, *, start=None, home=None) -> "KBPool":
        """Build a pool over every KB in the resolved registry manifest, and keep WATCHING that
        manifest: a server that outlives an edit must serve what the file says now, not what it
        said at startup."""
        manifest = registry.manifest_path(explicit, start=start, home=home)
        return cls(registry.load(explicit, start=start, home=home), manifest=manifest)

    @classmethod
    def single(cls, cfg: Config) -> "KBPool":
        """A one-KB pool wrapping an already-loaded `Config` (legacy `--config` mode). Its id is
        `cfg.id`, pre-cached so it is never reloaded, and it is the pool's default."""
        pool = cls((KB(id=cfg.id, path=cfg.config_path),))
        pool._cache[cfg.id] = cfg
        return pool
