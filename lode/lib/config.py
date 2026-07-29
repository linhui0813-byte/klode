"""Load and resolve a `library.toml` into a validated `Config`.

lodlib is a port of a knowledge-library design that lived inside one project as three
scripts with hard-coded module constants (`ROOT`, `LIB`, `CARDS`, `SHELVES`, `BIB`).
Everything project-specific now comes from ONE hand-edited TOML file that sits at the
root of a library. This module finds it, reads it (stdlib `tomllib`, Python 3.11+, zero
dependencies), applies defaults, validates, and hands the rest of the package a frozen
`Config` with every path pre-resolved to an absolute `Path`.

Fail loud: a malformed config raises `ConfigError` at load time, never a confusing
`FileNotFoundError` three modules deep.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_NAME = "library.toml"


class ConfigError(Exception):
    """A `library.toml` is missing, malformed, or internally inconsistent."""


@dataclass(frozen=True)
class Config:
    # --- provenance ---
    config_path: Path          # the library.toml itself
    root: Path                 # dir containing library.toml (all rel paths resolve from here)

    # --- library layout ---
    lib: Path                  # root / [library].dir      — holds the shelves + cards
    lib_rel: str               # posix relpath of lib from root (used in card `file:` fields)
    cards: Path                # lib / [library].cards
    shelves: tuple[str, ...]   # the taxonomy: subdirs of lib holding source .txt files

    # --- bibliography (optional) ---
    bib_enabled: bool
    bib: Path | None           # lib / [bibliography].path, or None when disabled

    # --- framework/synthesis layer (optional, off by default) ---
    fw_enabled: bool
    frameworks: Path | None    # lib / [frameworks].dir
    syntheses: Path | None     # frameworks / [frameworks].syntheses

    # --- copyright-leak guard ---
    copyright_guard: bool
    guard_relpaths: tuple[str, ...]   # repo-relative (posix, from root) dirs for `git ls-files`

    # --- normalizer ---
    backup_dir: Path | None    # None => system temp dir
    backup_keep: int
    dict_path: str

    # ------------------------------------------------------------------
    @classmethod
    def find(cls, start: Path | None = None) -> Path:
        """Walk up from `start` (default: cwd) to the nearest `library.toml`."""
        here = (start or Path.cwd()).resolve()
        for d in (here, *here.parents):
            cand = d / CONFIG_NAME
            if cand.is_file():
                return cand
        raise ConfigError(
            f"no {CONFIG_NAME} found in {here} or any parent — run `lib init` to scaffold one"
        )

    @classmethod
    def load(cls, config_path: Path | None = None, *, start: Path | None = None) -> "Config":
        path = (config_path or cls.find(start)).resolve()
        try:
            with open(path, "rb") as f:
                raw = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{path}: invalid TOML — {e}") from e
        except OSError as e:
            raise ConfigError(f"{path}: cannot read — {e}") from e

        root = path.parent

        lib_section = raw.get("library", {})
        lib_dirname = str(lib_section.get("dir", "library"))
        lib = (root / lib_dirname).resolve()
        try:
            lib_rel = lib.relative_to(root).as_posix()
        except ValueError:
            raise ConfigError(
                f"[library].dir ({lib_dirname!r}) must be inside the config directory {root}"
            )

        cards = (lib / str(lib_section.get("cards", "cards"))).resolve()

        raw_shelves = lib_section.get("shelves", [])
        if raw_shelves and not isinstance(raw_shelves, list):
            raise ConfigError('[library].shelves must be a list of directory names, '
                              'e.g. ["books", "papers"] (a bare string is iterated into letters)')
        shelves = tuple(raw_shelves or [])
        if not shelves:
            raise ConfigError("[library].shelves must list at least one shelf directory name")
        for s in shelves:
            if not isinstance(s, str) or "/" in s or s in ("", ".", ".."):
                raise ConfigError(f"[library].shelves entry {s!r} must be a simple directory name")

        bib_section = raw.get("bibliography", {})
        bib_enabled = bool(bib_section.get("enabled", True))
        bib = (lib / str(bib_section.get("path", "BIBLIOGRAPHY.md"))).resolve() if bib_enabled else None

        fw_section = raw.get("frameworks", {})
        fw_enabled = bool(fw_section.get("enabled", False))
        frameworks = (lib / str(fw_section.get("dir", "frameworks"))).resolve() if fw_enabled else None
        syntheses = (
            (frameworks / str(fw_section.get("syntheses", "_syntheses"))).resolve()
            if fw_enabled and frameworks else None
        )

        cp_section = raw.get("copyright", {})
        copyright_guard = bool(cp_section.get("guard", True))
        extra = cp_section.get("extra_guard_dirs", []) or []
        if extra and not isinstance(extra, list):
            raise ConfigError("[copyright].extra_guard_dirs must be a list of directory names")
        for d in extra:
            if not isinstance(d, str) or "/" in d or d in ("", ".", ".."):
                raise ConfigError(f"[copyright].extra_guard_dirs entry {d!r} must be a simple directory name")
        guard = list(shelves) + [str(d) for d in extra]
        try:
            guard_relpaths = tuple((lib / g).resolve().relative_to(root).as_posix() for g in guard)
        except ValueError as e:
            raise ConfigError(f"a guard dir resolves outside the library root: {e}")

        nz = raw.get("normalize", {})
        backup_raw = str(nz.get("backup_dir", "") or "").strip()
        backup_dir = (root / backup_raw).resolve() if backup_raw else None
        try:
            backup_keep = int(nz.get("backup_keep", 3))
        except (TypeError, ValueError):
            raise ConfigError(f"[normalize].backup_keep must be an integer, got {nz.get('backup_keep')!r}")
        dict_path = str(nz.get("dict_path", "/usr/share/dict/words"))

        return cls(
            config_path=path, root=root,
            lib=lib, lib_rel=lib_rel, cards=cards, shelves=shelves,
            bib_enabled=bib_enabled, bib=bib,
            fw_enabled=fw_enabled, frameworks=frameworks, syntheses=syntheses,
            copyright_guard=copyright_guard, guard_relpaths=guard_relpaths,
            backup_dir=backup_dir, backup_keep=backup_keep, dict_path=dict_path,
        )
