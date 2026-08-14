"""Load and resolve a `library.toml` into a validated `Config`.

klode is a port of a knowledge-library design that lived inside one project as three
scripts with hard-coded module constants (`ROOT`, `LIB`, `CARDS`, `SHELVES`, `BIB`).
Everything project-specific now comes from ONE hand-edited TOML file that sits at the
root of a library. This module finds it, reads it (stdlib `tomllib`, Python 3.11+, zero
dependencies), applies defaults, validates, and hands the rest of the package a frozen
`Config` with every path pre-resolved to an absolute `Path`.

Fail loud: a malformed config raises `ConfigError` at load time, never a confusing
`FileNotFoundError` three modules deep.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_NAME = "library.toml"

# A KB id (and any name derived into a server/tool prefix) is a slug: lowercase
# alphanumerics joined by single hyphens — no leading/trailing/double hyphen. One
# definition, shared by the config validator and the MCP server-name derivation, so
# the two cannot drift.
_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def slugify(text: str) -> str:
    """Best-effort slug: lowercase, collapse any run of non-alphanumerics to one hyphen, trim."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean_line(text: str) -> str:
    """Neutralize a one-line human field (name/description): drop control bytes — a terminal-escape
    and catalog-spoofing vector once rendered — and collapse all whitespace, newlines included, to
    single spaces, so the value can never inject extra lines into a catalog listing."""
    return " ".join(_CTRL_RE.sub(" ", text).split())


class ConfigError(Exception):
    """A `library.toml` is missing, malformed, or internally inconsistent."""


def _as_path(value, label: str) -> Path | None:
    """Coerce a public path parameter, RESOLVED, or None — failing as a `ConfigError`.

    `Config.load` and `Config.find` are the public facade's entry points and the obvious way to
    call them is with a string. Every one of them did `.resolve()` on the argument, so a `str`
    raised `AttributeError: 'str' object has no attribute 'resolve'` — three modules from anything
    a caller could act on, and precisely the confusing-error-deep-inside failure this module opens
    by promising not to produce. Resolution itself can also raise a bare `ValueError` (an embedded
    null byte), which escaped the same contract.
    """
    if value is None:
        return None
    try:
        return Path(value).resolve()
    except TypeError as e:
        raise ConfigError(f"{label} must be a path or a string, got "
                          f"{type(value).__name__}") from e
    except (OSError, ValueError) as e:
        raise ConfigError(f"{label} {value!r} is not a usable path — {e}") from e


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
    criteria: Path | None      # frameworks / [frameworks].criteria — authored CriterionSpec v1 rubrics
    #                            (the gate's sole input; see dev-docs/SPEC-criterion.md)

    # --- copyright-leak guard ---
    copyright_guard: bool
    guard_relpaths: tuple[str, ...]   # repo-relative (posix, from root) dirs for `git ls-files`

    # --- normalizer ---
    backup_dir: Path | None    # None => system temp dir
    backup_keep: int
    dict_path: str

    # --- identity (self-describing KB; every field optional in TOML) ---
    id: str                    # slug; defaults to a slug of the config directory name
    name: str                  # human-facing title ("" when unset)
    description: str           # one-line purpose ("" when unset)
    tags: tuple[str, ...]      # optional domain tags

    # ------------------------------------------------------------------
    @classmethod
    def find(cls, start: Path | str | None = None) -> Path:
        """Walk up from `start` (default: cwd) to the nearest `library.toml`."""
        here = _as_path(start, "start") or Path.cwd()
        for d in (here, *here.parents):
            cand = d / CONFIG_NAME
            if cand.is_file():
                return cand
        raise ConfigError(
            f"no {CONFIG_NAME} found in {here} or any parent — run `klode init` to scaffold one"
        )

    @classmethod
    def load(cls, config_path: Path | str | None = None, *,
             start: Path | str | None = None) -> "Config":
        path = _as_path(config_path, "config_path") or cls.find(start)
        try:
            with open(path, "rb") as f:
                raw = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{path}: invalid TOML — {e}") from e
        except UnicodeDecodeError as e:
            raise ConfigError(f"{path}: not valid UTF-8 — {e}") from e
        except OSError as e:
            raise ConfigError(f"{path}: cannot read — {e}") from e

        root = path.parent

        def _table(key: str) -> dict:
            """Fetch a config section, failing loud if it is present but not a TOML table — so a
            malformed `library = "x"` raises ConfigError here, not an AttributeError three modules deep."""
            v = raw.get(key, {})
            if not isinstance(v, dict):
                raise ConfigError(f"[{key}] must be a table (got {type(v).__name__})")
            return v

        lib_section = _table("library")
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
            # A shelf name reaches `git ls-files` as a pathspec. `--` at the call site stops it
            # being read as an option; refusing the shape here means the guard is not the only
            # thing standing between a shelf called `--others` and a silent copyright leak.
            if s.startswith("-"):
                raise ConfigError(f"[library].shelves entry {s!r} must not begin with '-' — it is "
                                  "passed to git as a path, where a leading dash reads as a flag")

        bib_section = _table("bibliography")
        bib_enabled = bool(bib_section.get("enabled", True))
        bib = (lib / str(bib_section.get("path", "BIBLIOGRAPHY.md"))).resolve() if bib_enabled else None

        fw_section = _table("frameworks")
        fw_enabled = bool(fw_section.get("enabled", False))
        frameworks = (lib / str(fw_section.get("dir", "frameworks"))).resolve() if fw_enabled else None
        syntheses = (
            (frameworks / str(fw_section.get("syntheses", "_syntheses"))).resolve()
            if fw_enabled and frameworks else None
        )
        criteria = None
        if fw_enabled and frameworks:
            crit_rel = str(fw_section.get("criteria", "_criteria"))
            criteria = (frameworks / crit_rel).resolve()
            # The rubric dir is the gate's root of trust; an absolute or `../` value would relocate
            # canonical rubric loading outside the framework tree the rest of the config describes.
            if not criteria.is_relative_to(frameworks.resolve()):
                raise ConfigError(
                    f"[frameworks].criteria = {crit_rel!r} resolves outside the frameworks dir "
                    f"({frameworks}) — it must be a relative name inside it")

        cp_section = _table("copyright")
        copyright_guard = bool(cp_section.get("guard", True))
        extra = cp_section.get("extra_guard_dirs", []) or []
        if extra and not isinstance(extra, list):
            raise ConfigError("[copyright].extra_guard_dirs must be a list of directory names")
        for d in extra:
            if not isinstance(d, str) or "/" in d or d in ("", ".", ".."):
                raise ConfigError(f"[copyright].extra_guard_dirs entry {d!r} must be a simple directory name")
            if d.startswith("-"):                     # same pathspec rule as [library].shelves
                raise ConfigError(f"[copyright].extra_guard_dirs entry {d!r} must not begin with "
                                  "'-' — it is passed to git as a path, where a leading dash "
                                  "reads as a flag")
        guard = list(shelves) + [str(d) for d in extra]
        try:
            guard_relpaths = tuple((lib / g).resolve().relative_to(root).as_posix() for g in guard)
        except ValueError as e:
            raise ConfigError(f"a guard dir resolves outside the library root: {e}")
        # Validate the value git will ACTUALLY receive. Checking the raw shelf names left the
        # composed path unguarded: `[library].dir = "--format="` with an ordinary shelf produced
        # the pathspec `--format=/books`, which `git ls-files` accepts as an option and which
        # hides every tracked file. Whatever assembles a pathspec, the pathspec is what must not
        # look like a flag.
        for rel in guard_relpaths:
            if rel.startswith("-"):
                raise ConfigError(
                    f"the copyright guard would pass {rel!r} to git as a path, where a leading "
                    "dash reads as a flag — rename [library].dir or the shelf so the composed "
                    "path does not begin with '-'")

        # identity — self-describing KB metadata, all optional and backward-compatible.
        # A config with no [library].id still loads: id falls back to a slug of the dir name.
        raw_id = lib_section.get("id")
        if raw_id is None:
            kb_id = slugify(root.name)
            if not kb_id:
                # a dir name with no sluggable (ASCII alnum) content can't yield a distinct id;
                # fail loud rather than collapse several such KBs to the same fallback name.
                raise ConfigError(
                    f"could not derive a KB id from directory name {root.name!r} — set an explicit "
                    "[library].id (a slug: lowercase letters, digits, single hyphens)")
        else:
            if not isinstance(raw_id, str):
                raise ConfigError("[library].id must be a string slug (lowercase a-z, 0-9, hyphens)")
            kb_id = raw_id.strip()
            if not _SLUG_RE.fullmatch(kb_id):
                raise ConfigError(
                    f"[library].id {raw_id!r} is not a valid slug — use lowercase letters, digits, "
                    "and single hyphens (no leading/trailing/double hyphen)")
        name = lib_section.get("name", "")
        if not isinstance(name, str):
            raise ConfigError("[library].name must be a string")
        name = _clean_line(name)
        description = lib_section.get("description", "")
        if not isinstance(description, str):
            raise ConfigError("[library].description must be a string")
        description = _clean_line(description)
        raw_tags = lib_section.get("tags", [])
        if not isinstance(raw_tags, list):
            raise ConfigError('[library].tags must be a list of strings, e.g. ["fiction", "craft"]')
        for t in raw_tags:
            if not isinstance(t, str):
                raise ConfigError(f"[library].tags entry {t!r} must be a string")
        tags = tuple(t.strip() for t in raw_tags if t.strip())

        nz = _table("normalize")
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
            fw_enabled=fw_enabled, frameworks=frameworks, syntheses=syntheses, criteria=criteria,
            copyright_guard=copyright_guard, guard_relpaths=guard_relpaths,
            backup_dir=backup_dir, backup_keep=backup_keep, dict_path=dict_path,
            id=kb_id, name=name, description=description, tags=tags,
        )
