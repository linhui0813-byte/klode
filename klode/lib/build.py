"""Build/refresh the library card index — one card per source, with Levels of Zoom.

A *source card* is the neutral, per-source record that exposes a source at four levels:

  L0 meta   — front-matter (id/shelf/file/framework/zoom/aliases)      [this module fills it]
  L1 thin   — 1-3 sentences: the core engine                          [OWED — grep-grounded, by hand]
  L2 full   — main points outlined, grep-cited                        [OWED — or: see the framework card]
  L3 content— the .txt itself (git-ignored, grep-ready)               [pointer only, never duplicated]

The generator is a SCAFFOLDER, not a summarizer: it never invents L1/L2 (that is the drift
trap). It enumerates the shelf source files (the authoritative list), writes/updates only the
front-matter + Content pointer + Bibliography excerpt, and leaves any existing hand-written
Thin/Full body untouched. Extraction (grep-grounded, by hand) fills L1/L2.

Idempotent. Emits `cards/INDEX.md` (the human board) and returns a coverage report.
"""
from __future__ import annotations

import hashlib
import os
import re

from .common import (MARK, NON_CARDS, body_after_marker, fm_get, front_matter,
                      glob_in, read)
from .config import Config, ConfigError


def source_sha256(path: str) -> str:
    """Hash of a source file's raw bytes — the freshness baseline. Stamped into a card's
    front-matter by `klode build --stamp`; `klode check` warns when the live source no longer
    matches, so claims written against an older version get re-verified (freshness ≠ rot)."""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def framework_source_map(cfg: Config) -> dict[str, str]:
    """source .txt relpath -> framework-card relpath, parsed from each framework card's source
    reference (precise — avoids prefix false positives). Empty unless the framework layer is on."""
    out: dict[str, str] = {}
    if not (cfg.fw_enabled and cfg.frameworks and cfg.frameworks.is_dir()):
        return out
    fw_rel = cfg.frameworks.relative_to(cfg.root).as_posix()
    shelves = "|".join(re.escape(s) for s in cfg.shelves)
    # charset must match common.src_path_re, else an uppercase/underscore source (e.g. Booth_1961.txt)
    # passes `klode check` but is invisible here — its framework link and consult de-dup silently break
    src_re = re.compile(rf"{re.escape(cfg.lib_rel)}/(?:{shelves})/[A-Za-z0-9._-]+\.txt")
    for p in glob_in(cfg.frameworks, "*.md"):
        b = os.path.basename(p)
        if b == "README.md":
            continue
        m = src_re.search(read(p))
        if m:
            out[m.group(0)] = f"{fw_rel}/{b}"
    return out


def title_from_bib(bib: str | None) -> str | None:
    """Real title from a bibliography line: text before the filename backtick, emphasis stripped."""
    if not bib:
        return None
    head = bib.split("`")[0].strip().rstrip("|").strip()
    head = head.replace("**", "").replace("*", "").strip()
    return head or None


def bib_line_for(cfg: Config, stem: str) -> str | None:
    """Best-effort: the raw bibliography catalog row for this source's filename/stem. Prefer an
    actual table row (starts with '|') over prose mentions (grep-integrity notes name the file too)."""
    if not (cfg.bib and cfg.bib.exists()):
        return None
    matches = []
    with open(cfg.bib, encoding="utf-8") as f:
        for line in f:
            # boundary-anchored: `plato` must NOT match a `plato-republic` row (prefix collision
            # would give plato.md the wrong title + bibliography line, and defeat the G mirror check)
            if f"{stem}.txt" in line or re.search(rf"`{re.escape(stem)}(?=[.`])", line):
                matches.append(line.strip())
    if not matches:
        return None
    rows = [m for m in matches if m.lstrip().startswith("|")]
    pick = rows[0] if rows else matches[0]
    return pick.lstrip("|").strip()


def humanize(stem: str) -> str:
    s = re.sub(r"-\d{4}.*$", "", stem)         # drop trailing -YEAR-...
    s = s.replace("-", " ").strip()
    return s[:1].upper() + s[1:]


def _enumerate_sources(cfg: Config) -> list[dict]:
    fwmap = framework_source_map(cfg)
    sources = []
    for shelf in cfg.shelves:
        for p in sorted(glob_in(cfg.lib, shelf, "*.txt")):
            stem = os.path.basename(p)[:-4]
            rel = f"{cfg.lib_rel}/{shelf}/{stem}.txt"
            bib = bib_line_for(cfg, stem)
            sources.append(dict(id=stem, shelf=shelf, file=rel, framework=fwmap.get(rel),
                                bib=bib, title=title_from_bib(bib) or humanize(stem)))
    ids = [s["id"] for s in sources]        # the card namespace is flat: same stem on two shelves
    dups = sorted({i for i in ids if ids.count(i) > 1})   # would collide onto one card, silently
    if dups:
        raise ConfigError(f"source filename stems collide across shelves: {', '.join(dups)} — "
                          "rename so each source has a unique stem (cards are keyed by stem only)")
    return sources


def _on_disk(directory, suffix: str, exclude=()) -> list[str] | None:
    """Names in `directory` matching `suffix`, by the same rules `glob` applies (no dotfiles,
    case-insensitive suffix — Windows globs `CARD.MD` against `*.md` while `endswith` does not).
    None when the directory cannot be read."""
    try:
        return [e.name for e in os.scandir(directory)
                if e.is_file() and e.name.lower().endswith(suffix)
                and not e.name.startswith(".") and e.name not in exclude]
    except OSError:
        return None


def _refuse_if_enumeration_disagrees(cfg: Config, existing: list, sources: list) -> None:
    """Refuse to rewrite the board from an enumeration that disagrees with the disk.

    `check` only reports; THIS rewrites INDEX.md and every card, so the write side is where a
    blind enumeration becomes irreversible. Three enumerations feed it and all three could fail
    open independently:

      * cards — a metacharacter path returned [] and the board was overwritten empty;
      * SOURCES — a partial shelf scan stays truthy, so `sources` looked fine and the board was
        rebuilt missing whatever the scan dropped, with no guard firing at all;
      * frameworks — a swallowed read error becomes an empty map, and build then writes
        `framework: none` over valid card metadata and drops the board links.
    """
    if cfg.cards.is_dir() and not existing:
        names = _on_disk(cfg.cards, ".md", NON_CARDS)
        if names is None:
            raise ConfigError(f"cannot read the cards directory {cfg.cards} — refusing to rewrite "
                              "the board from an enumeration that could not run")
        if names:
            raise ConfigError(
                f"card enumeration returned nothing while {len(names)} card file(s) sit in "
                f"{cfg.cards} ({', '.join(sorted(names)[:3])}…) — refusing to rewrite the board "
                "from an enumeration that disagrees with the directory. This is a bug in klode.")

    seen = {s["id"] for s in sources}
    for shelf in cfg.shelves:
        d = cfg.lib / shelf
        if not d.is_dir():
            continue
        names = _on_disk(d, ".txt")
        if names is None:
            raise ConfigError(f"cannot read the shelf directory {d} — refusing to rewrite the "
                              "board from a corpus scan that could not run")
        missed = {n[:-4] for n in names} - seen
        if missed:
            raise ConfigError(
                f"source enumeration missed {len(missed)} file(s) in {d} "
                f"({', '.join(sorted(missed)[:3])}…) — refusing to rebuild the board from a "
                "partial corpus scan. This is a bug in klode, not an incomplete shelf.")

    if cfg.fw_enabled and cfg.frameworks and cfg.frameworks.is_dir():
        names = _on_disk(cfg.frameworks, ".md", ("README.md",))
        if names is None:
            raise ConfigError(f"cannot read the frameworks directory {cfg.frameworks} — refusing "
                              "to rewrite card metadata from an enumeration that could not run")
        enumerated = {os.path.basename(p) for p in glob_in(cfg.frameworks, "*.md")}
        missed = set(names) - enumerated
        if missed:
            raise ConfigError(
                f"framework enumeration missed {len(missed)} file(s) in {cfg.frameworks} "
                f"({', '.join(sorted(missed)[:3])}…) — refusing to rewrite cards, which would "
                "record `framework: none` over links that exist. This is a bug in klode.")


def build(cfg: Config, *, stamp: bool = False) -> dict:
    """Scaffold/refresh every card and the board. Returns a stats dict. `stamp` (re)computes each
    installed source's freshness hash — the author's "I re-verified against the current source"
    action; without it, an existing hash is carried across unchanged so real drift stays visible."""
    os.makedirs(cfg.cards, exist_ok=True)
    sources = _enumerate_sources(cfg)

    existing = [p for p in glob_in(cfg.cards, "*.md")
                if os.path.basename(p) not in NON_CARDS]
    # The write-side of the enumeration tripwire, and the one that actually matters. `check` only
    # reports; THIS rewrites INDEX.md. The guard below covers "no sources but cards exist"; it
    # cannot see the case where BOTH enumerations come back empty because enumeration itself
    # failed — which is exactly what a metacharacter path did, and what an unreadable directory
    # would still do. Verified: with enumeration stubbed empty, build overwrote a 2-card INDEX
    # with an empty one and reported success.
    _refuse_if_enumeration_disagrees(cfg, existing, sources)
    if not sources and existing:
        # Fresh clone: the git-ignored corpus is not installed. Do NOT rewrite cards or overwrite
        # the tracked INDEX.md with an empty board — that is silent data loss AND would make the
        # next `klode check` fail [D] telling the user to run the very command that emptied it.
        return dict(created=0, updated=0, total=0, stamped=0, framework_linked=0,
                    by_zoom={}, aliased=0, index=os.path.join(cfg.cards, "INDEX.md"),
                    skipped="corpus not installed (0 shelf sources) — cards and board left unchanged")

    created = updated = stamped = 0
    for s in sources:
        path = os.path.join(cfg.cards, s["id"] + ".md")
        old_body = None
        zoom = "stub"
        aliases = "[]"    # concept-alias line, carried across runs like `zoom` (never invented here)
        sha = review_by = superseded_by = None   # freshness: carried across runs, never invented
        if os.path.exists(path):
            old = read(path)
            old_body = body_after_marker(old)
            if old_body is None and "\n## Thin" in old:
                # Fail-safe: no recognized marker, but the card already has a body — NEVER
                # stub over hand-written content. Salvage everything from the first heading.
                old_body = old[old.index("\n## Thin"):]
            fm = front_matter(old)
            mz = re.search(r"^zoom:\s*(\w+)", fm, re.M)
            if mz:
                zoom = mz.group(1)
            ma = re.search(r"^aliases:\s*(.+)$", fm, re.M)
            if ma:
                aliases = ma.group(1).strip()
            sha, review_by, superseded_by = (fm_get(fm, "source_sha256"),
                                             fm_get(fm, "review_by"), fm_get(fm, "superseded_by"))
        src_abs = os.path.join(cfg.root, s["file"])
        if stamp and os.path.exists(src_abs):
            new_sha = source_sha256(src_abs)
            stamped += new_sha != sha    # count genuine (re)stamps for the report
            sha = new_sha
        fm_lines = [
            "---",
            f"id: {s['id']}",
            f"shelf: {s['shelf']}",
            f"file: {s['file']}",
            f"framework: {s['framework'] or 'none'}",
            f"zoom: {zoom}",   # stub (L0) | thin (L0+L1) | full (L0+L1+L2) — bumped by hand when filled
            f"aliases: {aliases}",   # [term, …] concept synonyms for grep recall — hand-filled, never invented
            "grep_ready: true",
        ]
        # freshness (all optional): source hash is opt-in via --stamp; review_by/superseded_by
        # are hand-filled and only carried across — build never invents them.
        if sha:
            fm_lines.append(f"source_sha256: {sha}")
        if review_by:
            fm_lines.append(f"review_by: {review_by}")
        if superseded_by:
            fm_lines.append(f"superseded_by: {superseded_by}")
        fm_lines.append("---")
        head = "\n".join(fm_lines) + f"\n\n# {s['title']}\n\n"
        if s["bib"]:
            head += f"**Bibliography.** {s['bib']}\n\n"
        head += ("## Content\n"
                 f"`{s['file']}` — full text (git-ignored, grep-ready). Never duplicated here; grep it to verify.\n\n")
        if s["framework"]:
            head += (f"> A per-dimension distillation of this source exists: `{s['framework']}` "
                     "(interpretation for a dimension, not a neutral L2 — see it for the full analysis).\n\n")
        head += MARK + "\n"
        default_body = (
            "\n## Thin\n_(L1 owed — 1-3 sentences, grep-grounded to the .txt)_\n"
            "\n## Full\n_(L2 owed — main points outlined, grep-cited"
            + (" — or defer to the framework card above)_\n" if s["framework"] else ")_\n")
        )
        body = old_body if old_body is not None else default_body
        # Collapse blank-line runs only in the machine-managed head and normalize the seam; the
        # hand-written body is joined VERBATIM (blank lines inside a code fence must survive — the
        # SPEC promises build never touches below-marker prose). Idempotent: old_body already begins
        # after the marker, so the seam re-normalizes to exactly one blank line each run.
        content = re.sub(r"\n{3,}", "\n\n", head).rstrip("\n") + "\n\n" + body.lstrip("\n")
        if not content.endswith("\n"):
            content += "\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        if old_body is None:
            created += 1
        else:
            updated += 1

    _write_index(cfg, sources)

    by_zoom = _zoom_counts(cfg, sources)
    fw_linked = sum(1 for s in sources if s["framework"])
    aliased = _aliased_count(cfg, sources)
    return dict(created=created, updated=updated, total=len(sources), stamped=stamped,
                framework_linked=fw_linked, by_zoom=by_zoom, aliased=aliased,
                index=os.path.join(cfg.cards, "INDEX.md"))


def _card_zoom(cfg: Config, sid: str) -> str:
    m = re.search(r"^zoom:\s*(\w+)", read(os.path.join(cfg.cards, sid + ".md")), re.M)
    return m.group(1) if m else "stub"


def _zoom_counts(cfg: Config, sources: list[dict]) -> dict[str, int]:
    by_zoom: dict[str, int] = {}
    for s in sources:
        z = _card_zoom(cfg, s["id"])
        by_zoom[z] = by_zoom.get(z, 0) + 1
    return by_zoom


def _aliased_count(cfg: Config, sources: list[dict]) -> int:
    n = 0
    for s in sources:
        a = re.search(r"^aliases:\s*(.+)$", read(os.path.join(cfg.cards, s["id"] + ".md")), re.M)
        if a and a.group(1).strip() not in ("[]", ""):
            n += 1
    return n


def _write_index(cfg: Config, sources: list[dict]) -> None:
    by_zoom = _zoom_counts(cfg, sources)
    aliased = _aliased_count(cfg, sources)
    idx = ["# Library card index (the board)\n",
           "One card per source, with Levels of Zoom. `zoom`: **stub** (meta only) · **thin** (+1-3 "
           "sentence summary) · **full** (+main points, grep-cited). L1/L2 are grep-grounded, filled on "
           "demand — never invented. Generated by klode.\n",
           f"\n**{len(sources)} sources** · "
           + " · ".join(f"{k}: {v}" for k, v in sorted(by_zoom.items()))
           + f" · aliased: {aliased}\n"]
    for shelf in cfg.shelves:
        rows = [s for s in sources if s["shelf"] == shelf]
        if not rows:
            continue
        idx.append(f"\n## {shelf} ({len(rows)})\n")
        idx.append("| card | zoom | framework |\n|------|------|-----------|")
        for s in rows:
            cz = _card_zoom(cfg, s["id"])
            fwl = (f"[fw]({os.path.relpath(os.path.join(cfg.root, s['framework']), cfg.cards)})"
                   if s["framework"] else "—")
            idx.append(f"| [{s['id']}]({s['id']}.md) | {cz} | {fwl} |")
        idx.append("")
    with open(os.path.join(cfg.cards, "INDEX.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(idx) + "\n")
