"""`klode` — the command-line entry point for a klode library.

  klode init        scaffold a new library (library.toml + dirs + .gitignore)
  klode build       scaffold/refresh cards + the board (idempotent)
  klode check       integrity + citation-rot linter (exit 1 on any ERROR)
  klode normalize   grep-readiness preprocessor for the corpus .txt files
  klode search      L0/L1 retrieval — "which sources are relevant?"
  klode zoom        pull one level of a card — meta | thin | full | content
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path

from builtins import print as _builtin_print

from . import console, core, query, services
from .config import Config, ConfigError
from .pool import KBPool

# ---------------------------------------------------------------------------
# init — scaffold a fresh library
# ---------------------------------------------------------------------------
_TOML_TEMPLATE = """\
# library.toml — a klode knowledge library.
# One card per source, exposed at four Levels of Zoom (L0 meta / L1 thin / L2 full / L3 content).
# See SPEC.md for the card format and the two disciplines that keep it from rotting.

[library]
dir = "library"          # library root, relative to this file
cards = "cards"          # cards subdir, relative to the library dir
shelves = [{shelves}]    # the taxonomy: subdirs of the library dir holding source .txt files

[bibliography]
enabled = true
path = "BIBLIOGRAPHY.md"  # relative to the library dir

[frameworks]              # optional per-dimension distillation layer
enabled = false
dir = "frameworks"
syntheses = "_syntheses"

[copyright]
guard = true              # fail `klode check` if any guarded .txt/.pdf is git-tracked
extra_guard_dirs = []     # extra dirs (relative to the library dir) to also guard

[normalize]
backup_dir = ""           # empty => system temp dir (kept OUTSIDE the tree)
backup_keep = 3
dict_path = "/usr/share/dict/words"
"""

# Delimited so the managed rules can be REPLACED, not just appended once. Without an end marker,
# `init --force` with a new shelf left that shelf's copyrighted .txt/.pdf untracked-but-unignored —
# one `git add -A` from committing the corpus this project exists to keep local.
# The installed console-script name, and the ONLY place it is spelled. Four "next command" hints
# still said `lib` — the name of the pre-rename tool these scripts were ported from. It survived
# review because `lib` exists at ~/.local/bin/lib on the machine klode was written on, so the
# printed hint ran there and was `command not found` for everyone else. One constant, shared with
# `ArgumentParser(prog=…)`, so the hints and the parser cannot disagree about what to call it.
PROG = "klode"

EXIT_ABSTAINED = 2   # measured-and-failed is 1; could-not-measure is 2, never 0
_GI_BEGIN = "# >>> klode managed: the corpus is copyrighted — sources stay local, cards are tracked"
_GI_END = "# <<< klode managed"
_GITIGNORE_BLOCK = "{begin}\n{lines}\n{end}\n"


def _gitignore_with_managed_block(existing: str, lines: str) -> str:
    """Replace klode's managed block in place, or append it. Everything outside the markers is the
    user's and is preserved byte for byte."""
    block = _GITIGNORE_BLOCK.format(begin=_GI_BEGIN, lines=lines, end=_GI_END)
    if _GI_BEGIN in existing and _GI_END in existing:
        head, rest = existing.split(_GI_BEGIN, 1)
        _managed, tail = rest.split(_GI_END, 1)
        return head + block.rstrip("\n") + tail
    # A pre-2026-08 unterminated block. Remove ONLY the contiguous run of `library/...` lines that
    # follows its header — that run is what klode wrote. Filtering every `library/...` line in the
    # whole file deleted user-authored rules that happened to start the same way, including ones
    # written before the marker; an independent verification caught exactly that.
    legacy = "# klode: the corpus is copyrighted"
    lines = existing.splitlines()
    for i, ln in enumerate(lines):
        if legacy in ln:
            j = i + 1
            while j < len(lines) and lines[j].startswith("library/"):
                j += 1
            existing = "\n".join(lines[:i] + lines[j:])
            break
    return (existing.rstrip() + "\n\n" + block) if existing.strip() else block


def cmd_init(args) -> int:
    root = Path(args.dir).resolve()
    cfg_path = root / "library.toml"
    if cfg_path.exists() and not args.force:
        print(f"refusing to overwrite existing {cfg_path} (use --force)", file=sys.stderr)
        return 1
    shelves = args.shelf or ["books", "papers"]
    for s in shelves:                                    # each shelf is a dir name AND a TOML string:
        if not s or s in (".", "..") or not all(c.isalnum() or c in "._-" for c in s):
            print(f"invalid shelf name {s!r}: use letters/digits and ._- only (one path component)",
                  file=sys.stderr)                        # reject `/`, `..`, quotes, newlines up front —
            return 2                                       # no TOML injection, no writing outside the project
    root.mkdir(parents=True, exist_ok=True)
    # Refuse to write THROUGH a symlink. In an attacker-prepared (or merely surprising) target
    # directory, a pre-existing `library` / `library.toml` / `.gitignore` symlink made init create
    # and overwrite files somewhere else entirely — verified: `init` wrote a shelf into the
    # symlink's target outside the requested root.
    managed_paths = [cfg_path, root / ".gitignore", root / "library",
                     root / "library" / "cards", root / "library" / "BIBLIOGRAPHY.md"]
    managed_paths += [root / "library" / sh for sh in shelves]
    for managed in managed_paths:
        if managed.is_symlink():
            print(f"refusing to write through the symlink {managed} -> "
                  f"{os.readlink(managed)}; remove it or choose another directory", file=sys.stderr)
            return 2
    cfg_path.write_text(
        _TOML_TEMPLATE.format(shelves=", ".join(f'"{s}"' for s in shelves)), encoding="utf-8")

    lib = root / "library"
    (lib / "cards").mkdir(parents=True, exist_ok=True)
    for s in shelves:
        (lib / s).mkdir(parents=True, exist_ok=True)
        (lib / s / ".gitkeep").touch()
    bib = lib / "BIBLIOGRAPHY.md"
    if not bib.exists():
        bib.write_text("# Bibliography\n\n| source | file | notes |\n|---|---|---|\n", encoding="utf-8")

    # .gitignore: keep the corpus local, track the cards
    ignore_lines = "\n".join(f"library/{s}/*.txt\nlibrary/{s}/*.pdf" for s in shelves)
    gi = root / ".gitignore"
    existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
    gi.write_text(_gitignore_with_managed_block(existing, ignore_lines), encoding="utf-8")

    print(f"scaffolded library at {root}")
    print(f"  config : {cfg_path.relative_to(root)}")
    print(f"  shelves: {', '.join(shelves)}")
    print("next: drop a .txt on a shelf, add a BIBLIOGRAPHY row, then `klode build`")
    return 0


# ---------------------------------------------------------------------------
# build / check / normalize — thin wrappers over the modules
# ---------------------------------------------------------------------------
def cmd_build(args) -> int:
    from . import build as build_mod
    cfg = _load(args)
    st = build_mod.build(cfg, stamp=getattr(args, "stamp", False))
    if st.get("skipped"):
        # Nothing was built. Exiting 0 let automation record a successful build for a run that
        # refreshed no card and wrote no board — the same success-on-work-not-done shape as
        # `check` printing OK without running citation-rot.
        print(st["skipped"])
        if getattr(args, "allow_unmeasured", False):
            print("(accepted via --allow-unmeasured)")
            return 0
        print("ABSTAINED: nothing was built — this is NOT a successful build "
              "(pass --allow-unmeasured to accept it)", file=sys.stderr)
        return EXIT_ABSTAINED
    print(f"cards: {st['created']} created, {st['updated']} refreshed, {st['total']} total")
    if st.get("stamped"):
        print(f"freshness: stamped {st['stamped']} source hash(es)")
    print(f"framework-linked (L2 available): {st['framework_linked']}")
    print("zoom coverage: " + ", ".join(f"{k}={v}" for k, v in sorted(st["by_zoom"].items())))
    print(f"alias coverage: {st['aliased']}/{st['total']} cards have concept aliases")
    print(f"board: {os.path.relpath(st['index'], cfg.root)}")
    return 0


def cmd_check(args) -> int:
    from . import check as check_mod
    if not getattr(args, "entail", False):
        for flag, val in (("--entail-model", args.entail_model),
                          ("--entail-threshold", args.entail_threshold)):
            if val not in (None, 0.5):
                # silently ignored without --entail, so a tightened threshold looked applied
                print(f"{flag} has no effect without --entail", file=sys.stderr)
                return 2
    entail_unavailable = ""
    cfg = _load(args)
    backend = None
    if getattr(args, "entail", False):
        from . import entail as entail_mod
        try:
            backend = entail_mod.load_backend(args.entail_model or entail_mod.DEFAULT_MODEL)
        except entail_mod.EntailUnavailable as e:
            # `--entail` was asked for EXPLICITLY. Printing a note and carrying on to `OK` reports
            # a check that never ran as a check that passed.
            print(f"  NOTE  entail skipped — {e}")
            entail_unavailable = str(e)
    r = check_mod.check(cfg, strict=getattr(args, "strict", False), entail=backend,
                        entail_threshold=getattr(args, "entail_threshold", 0.5))
    if entail_unavailable:
        r.unmeasured.append(f"--entail was requested but its backend could not load "
                            f"({entail_unavailable}) — no claim was scored")
    if not args.quiet:
        print(f"library check — {r.n_cards} cards, {r.n_txts} shelf sources")
    if not args.quiet:                      # `--quiet` said "only problems + summary" and then
        for nt in r.notes:                  # printed every informational note
            print(f"  NOTE  {nt}")
    for w in r.warns:
        print(f"  WARN  {w}")
    for e in r.errors:
        print(f"  ERROR {e}")
    for u in r.unmeasured:
        print(f"  UNMEASURED  {u}")
    if r.errors:
        print(f"\nFAIL: {len(r.errors)} error(s), {len(r.warns)} warning(s)")
        return 1
    if r.unmeasured and not getattr(args, "allow_unmeasured", False):
        # The word OK must not appear, and the exit code must not say success. CI reads the exit
        # code, never the notes — so `OK: 0 errors` on a run where citation-rot never executed was
        # a green build certifying nothing.
        print(f"\nABSTAINED: {len(r.unmeasured)} check(s) could not run — this is NOT a pass. "
              f"({len(r.warns)} warning(s))")
        return EXIT_ABSTAINED
    if r.unmeasured:
        print(f"\nOK (with {len(r.unmeasured)} check(s) knowingly skipped via --allow-unmeasured), "
              f"{len(r.warns)} warning(s)")
        return 0
    print(f"\nOK: 0 errors, {len(r.warns)} warning(s)")
    return 0


def cmd_ingest(args) -> int:
    from . import ingest as ingest_mod       # lazy: OCR deps aren't needed to import `klode`
    return ingest_mod.run(_load(args), args)


def cmd_normalize(args) -> int:
    from . import normalize as norm_mod
    cfg = _load(args)
    res = norm_mod.normalize(cfg, pattern=args.glob, apply=args.apply, stamp=args.stamp)
    if res.refused:
        print(res.refused, file=sys.stderr)
        return 2
    for s in res.skipped:
        print(f"SKIP {s}", file=sys.stderr)
    for rel, flags in res.details:
        print(f"\n{rel}")
        for fl in flags:
            print(f"    {fl}")
    verb = "changed" if args.apply else "would change"
    print(f"\n{'APPLIED' if args.apply else 'DRY RUN'}: {res.changed} file(s) {verb}")
    if res.backup_dir:
        print(f"backup at {res.backup_dir}")
        if res.pruned:
            print(f"pruned {res.pruned} old backup run(s), kept newest {cfg.backup_keep}")
    # `--check` makes a dry run a gate: nonzero when files are not normalized.
    if args.check and not args.apply and res.changed:
        return 1
    if args.check and not args.apply:
        # A gate that inspected NOTHING is not a passing gate. `0 file(s) would change` was
        # printed identically whether every file was clean or no file was read at all, and
        # skipped/unreadable inputs (invalid UTF-8 among them) were reported to stderr and then
        # ignored by the exit code.
        unmeasured = []
        if not res.details and res.changed == 0 and not res.scanned:
            unmeasured.append(f"no file matched {args.glob!r} — nothing was checked")
        if res.skipped:
            unmeasured.append(f"{len(res.skipped)} file(s) could not be read and were skipped")
        if unmeasured and not getattr(args, "allow_unmeasured", False):
            for u in unmeasured:
                print(f"UNMEASURED  {u}", file=sys.stderr)
            print("ABSTAINED: the normalize gate did not check what it claims to "
                  "(pass --allow-unmeasured to accept it)", file=sys.stderr)
            return EXIT_ABSTAINED
    return 0


# ---------------------------------------------------------------------------
# search / zoom — the LOD retrieval surface
# ---------------------------------------------------------------------------
def cmd_search(args) -> int:
    # Validate BEFORE choosing a renderer. The JSON branch returned first, so `--json search ""`
    # produced a successful empty result while prose rejected the identical input — the two
    # surfaces disagreeing about what a valid request even is.
    if not [t for t in args.query if t.strip()]:
        print("nothing to search for", file=sys.stderr)
        return 1
    if args.json:
        return _emit_json(_run(args, "search",
                               {"terms": args.query, "full": args.full, "limit": args.limit}))
    cfg = _load(args)
    hits, total = query.search(cfg, args.query, full=args.full, limit=args.limit)
    if not hits:
        print("no matching cards")
        return 0
    for h in hits:
        line = f"{h.id}  [{h.zoom}·{h.shelf}]  {h.score:.1f}"
        if h.gist:
            line += f"  — {h.gist[:110]}"
        print(line)
    if total > len(hits):
        print(f"… {total - len(hits)} more (raise --limit)")
    return 0


def _validate_zoom_args(args) -> str:
    """`--grep` and `--max` were accepted at every level and silently ignored above `content`, so a
    user asking to verify a claim at `--level thin` got a confident answer to a question that was
    never asked. An explicitly blank `--grep ""` was likewise treated as "no verification
    requested" and exited 0."""
    if args.level != "content":
        for flag, val in (("--grep", args.grep), ("--max", args.max)):
            if val is not None:
                return (f"{flag} applies only to --level content (got --level {args.level}); "
                        "it would have been silently ignored")
    else:
        if args.grep is not None and not args.grep.strip():
            return "--grep was given an empty phrase; pass a phrase, or omit --grep entirely"
        if args.max is not None and args.grep is None:
            return "--max limits the lines --grep shows; it does nothing without --grep"
    return ""


def cmd_zoom(args) -> int:
    if (bad := _validate_zoom_args(args)):
        print(bad, file=sys.stderr)
        return 2
    if args.json:
        if args.level == "content" and getattr(args, "grep", None):   # legacy zoom-verify combo
            return _emit_json(_run(args, "verify",
                                   {"card": args.id, "phrase": args.grep, "max_lines": args.max}))
        return _emit_json(_run(args, "zoom", {"id": args.id, "level": args.level}))
    cfg = _load(args)
    if not query.card_path(cfg, args.id):
        print(f"no card: {args.id} (try `klode search`)", file=sys.stderr)
        return 1
    level = args.level
    if level == "meta":
        print(query.meta(cfg, args.id))
        return 0
    if level in ("thin", "full"):
        body = query.body(cfg, args.id, level)
        if not body:
            print(f"[{level}] section not found in {args.id}", file=sys.stderr)
            return 1
        print(f"# {query.card_title(cfg, args.id)}  ({level})\n")
        print(body)
        return 0
    # content (L3): the source .txt itself
    src = query.source_of(cfg, args.id)
    if not src.installed:
        # A missing card exits 1 and a missing section exits 1, but an uninstalled source exited 0
        # — so a script asking "does this claim hold?" got success from a run that never opened a
        # file. Unavailable is unavailable whichever layer is missing.
        print(f"source not installed locally: {src.rel}\n"
              f"(the corpus is git-ignored — install it to grep L3)", file=sys.stderr)
        return EXIT_ABSTAINED
    if not args.grep:
        print(f"{src.rel} — {src.size} bytes, grep-ready. "
              f"Pass --grep \"phrase\" to verify a claim against it.")
        return 0
    # Through the SHARED service, like the JSON path. The prose branch called occurrence-only
    # `query.verify` directly, so it skipped the stamped-source freshness and ambiguity checks the
    # service applies — meaning stale or ambiguous evidence could be confirmed on one surface and
    # not the other, for the same request. That is precisely the drift `services.execute` exists to
    # prevent, in the one place the guarantee matters most.
    r = _run(args, "verify", {"id": args.id, "phrase": args.grep,
                              "max_lines": args.max or 10})
    v = r.value
    if v.found:
        for n, ln in v.lines:      # the real source line, with terminal control sequences neutered
            print(f"{n}: {ln}")
        if v.resolution is core.Resolution.FOLDED_ONLY:
            print(f"✓ resolves in {v.rel} (matches across line/hyphenation folding "
                  f"— not on a single line)")
        return 0
    if v.resolution is core.Resolution.SOURCE_STALE:
        # a distinction the direct call could not make: the source CHANGED since the card was
        # stamped, so an anchor that fails now may never have been wrong. Reporting this as
        # citation rot sends the reader to fix the wrong thing.
        print(f"source changed since this card was stamped: {v.rel}\n"
              f"(re-verify the claim, then `klode build --stamp`)", file=sys.stderr)
        return EXIT_ABSTAINED
    if v.resolution is core.Resolution.SOURCE_NOT_INSTALLED:
        # the shared service distinguishes "the source is not here" from "the phrase is not there";
        # the direct call could not, so both printed the same citation-rot message
        print(f"source not installed locally: {v.rel}", file=sys.stderr)
        return EXIT_ABSTAINED
    print(f"✗ NOT FOUND in {v.rel} — `{args.grep}` does not resolve (citation rot?)",
          file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# consult / diagnose — the writer's craft console over the frameworks/syntheses layer
# ---------------------------------------------------------------------------
_FW_DEFAULT = ["engine", "practices", "disagreement"]   # the moves a writer reaches for first


def _list_lenses(cfg: Config) -> int:
    dims, fws = query.lenses(cfg)
    if not dims and not fws:
        print("no frameworks/syntheses layer enabled ([frameworks] in library.toml).", file=sys.stderr)
        return 1
    if dims:
        print("craft dimensions — panels that keep the disagreements (consult by name):")
        for d in dims:
            tag = "" if d.status == "canonical" else f"  [{d.status}]"
            print(f"  {d.name:24}{tag}  {d.summary[:62]}")
    if fws:
        print("\nthinkers — one framework each:")
        by: dict[str, list[str]] = {}
        for f in fws:
            by.setdefault(f.dimension.split("—")[0].strip(), []).append(f.name)
        for dim, names in sorted(by.items()):
            print(f"  {dim[:20]:22} {', '.join(sorted(names))}")
    print(f"\nread one:  {PROG} consult <name> [--section spec] [--full]")
    print(f"stuck?     {PROG} diagnose \"what feels wrong\"")
    return 0


def _render_dimension(res: "console.ConsultResult", section: str | None, full: bool) -> int:
    v = res.view                                        # console resolved+loaded+projected; we only format
    print(f"# {v.name}   [{v.status}]")
    if v.core_question:
        print(f"core question: {v.core_question}")
    # writer default: the self-contained Craft layer, with engine/provenance hidden
    if not section and not full and res.selected:
        print(f"\n## Craft\n{res.selected[0][1]}")
        print("\n(engine mapping + provenance are below — `--full` for the whole synthesis, "
              "`--section <kw>` for one part)")
        return 0
    if v.cards:
        print(f"adjudicates across: {v.cards}")
    if full or (section and section.lower() == "all"):
        if v.gate:
            print(f"skeptic-gate: {v.gate}")
        print("\n" + v.body)
        return 0
    if section:
        if res.missing:
            print(f"\n(no section matching {section!r} — sections: {', '.join(v.secs)})", file=sys.stderr)
            return 1
        for h, b in res.selected:
            print(f"\n## {h}\n{b}")
        return 0
    print("\nsections (read one with `--section <kw>`, or `--full` for everything):")
    for h in v.secs:
        print(f"  • {h}")
    return 0


def _render_framework(fw: dict, section: str | None, full: bool) -> int:
    print(f"# {fw['title'] or fw['name']}")
    print(f"dimension: {fw['dimension']}")
    if fw["aliases"]:
        print(f"aliases: {fw['aliases']}")
    secs = fw["sections"]
    if full or (section and section.lower() == "all"):
        want = list(secs)
    elif section:
        want = [k for k in secs if section.lower() in k]
        if not want:
            print(f"\n(no section matching {section!r} — sections: {', '.join(secs)})", file=sys.stderr)
            return 1
    else:
        want = [k for k in _FW_DEFAULT if k in secs] or list(secs)
    for k in want:
        print(f"\n## {k}\n{secs[k]}")
    return 0


def _render_source(cfg: Config, cid: str, full: bool) -> int:
    """A source-only book (no framework lens): show its L1 (and L2 with --full)."""
    print(f"# {query.card_title(cfg, cid)}   (source card)")
    thin = query.body(cfg, cid, "thin")
    fullb = query.body(cfg, cid, "full")
    if thin:
        print(f"\n## Thin\n{thin}")
    if fullb and (full or not thin):
        print(f"\n## Full\n{fullb}")
    if not thin and not fullb:
        print("\n(no L1/L2 written yet)")
    print(f"\nverify against the source:  {PROG} zoom {cid} --level content --grep \"…\"")
    return 0


def _consult_projection(args) -> tuple[str, tuple[str, ...]]:
    """Which projection the flags ask for. ONE copy: the tree was duplicated verbatim in the JSON
    and prose branches, so the two surfaces could drift into projecting differently for identical
    flags — and nothing would have failed."""
    if args.section and args.section.lower() != "all":
        return "sections", (args.section,)
    if args.full or (args.section and args.section.lower() == "all"):
        return "full", ()
    return "writer", ()


def cmd_consult(args) -> int:
    if args.json:
        name = (args.name or "").strip()
        if not name:                                          # parity with prose: no name lists lenses
            return _emit_json(_run(args, "lenses.list", {}))
        proj, secs = _consult_projection(args)
        return _emit_json(_run(args, "consult", {"name": name, "projection": proj, "sections": secs}))
    cfg = _load(args)
    raw = (args.name or "").strip()
    if not raw:
        return _list_lenses(cfg)
    if args.section and args.section.lower() != "all":
        proj, secs = "sections", (args.section,)
    elif args.full or (args.section and args.section.lower() == "all"):
        proj, secs = "full", ()
    else:
        proj, secs = "writer", ()
    res = console.consult(cfg, console.ConsultRequest(raw, projection=proj, sections=secs))
    if res.outcome == "dimension":
        return _render_dimension(res, args.section, args.full)
    if res.outcome == "framework":
        return _render_framework(res.framework, args.section, args.full)
    if res.outcome == "source":
        return _render_source(cfg, res.source_id, args.full)
    # ambiguous / none
    print(res.note or f"no lens for {raw!r}.", file=sys.stderr)
    return 1


def cmd_kbs(args) -> int:
    """List the KBs registered in a manifest — a passive catalog (id + description). It lists and
    describes; it does not rank or recommend which KB to use. A broken entry is shown, not fatal."""
    if args.json:
        return _emit_json(services.execute(KBPool.from_registry(args.registry), "kbs.list", None, {}))
    from . import registry                        # lazy: registry is only needed for this subcommand
    entries = registry.load(args.registry)        # RegistryError (a ConfigError) → main() exit 2
    if not entries:
        print("no KBs registered — add one to a registry manifest "
              "(--registry PATH, ./.klode/registry.toml, or ~/.klode/registry.toml).")
        return 0
    infos = registry.catalog(entries)
    width = max(len(i.id) for i in infos)
    for i in infos:
        detail = (i.description or "(no description)") if i.ok else f"(unavailable: {i.error})"
        print(f"  {i.id:<{width}}  {detail}")
    return 0


def cmd_diagnose(args) -> int:
    if args.json:
        return _emit_json(_run(args, "diagnose", {"symptom": " ".join(args.symptom)}))
    cfg = _load(args)
    symptom = " ".join(args.symptom)
    result = query.diagnose(cfg, symptom)               # symptom -> [(dimension, core_question)]; logic in query
    if result is None:
        loc = f"{cfg.syntheses}/_diagnostics.md" if cfg.syntheses else "the syntheses dir"
        print(f"no diagnostics map — create {loc} (a `| symptom cues | dimensions |` table).", file=sys.stderr)
        return 1
    if not result:
        print("no symptom matched. Browse with `klode consult`, or `klode search <terms>`.", file=sys.stderr)
        return 1
    print(f"“{symptom.lower()}” → consult these panels (most-relevant first):\n")
    for dn, q in result:
        print(f"  • {dn}")
        if q:
            print(f"      {q[:88]}")
        print(f"      → {PROG} consult {dn}")              # §5: the writer default IS the Craft layer — no `--section spec`
    return 0


# ---------------------------------------------------------------------------
# Registry projection — consumption verbs route through `services.execute` (the shared core).
# This comment used to be false for prose `search`/`zoom`/`consult`/`diagnose`, which called
# `query`/`console` directly, and the stale-verification and --limit divergences an audit found
# were the proof. `zoom --grep` now goes through the service; `search`/`consult`/`diagnose` still
# render from `query`/`console` for prose but share the projection decision and are covered by an
# exit-code parity test over both surfaces (tests/test_parity.py).
# --json
# serializes the SAME structured OpResult the MCP renders as text; --kb addresses a registered KB.
# ---------------------------------------------------------------------------
def _load(args) -> Config:
    """The single Config a prose command renders. `--kb ID` addresses a registered KB via the
    registry so prose honours --kb exactly like --json/MCP do (no format-dependent KB); otherwise
    fall back to --config. `--kb '*'` (fan-out) is rejected for prose upstream in main()."""
    kb = getattr(args, "kb", None)
    if kb:
        return KBPool.from_registry(getattr(args, "registry", None)).config(kb)
    return Config.load(Path(args.config) if args.config else None)


def _pool(args) -> KBPool:
    if getattr(args, "kb", None):
        return KBPool.from_registry(getattr(args, "registry", None))
    return KBPool.single(_load(args))


def _jsonable(o):
    if isinstance(o, Enum):
        return o.value
    if is_dataclass(o):
        return {f.name: _jsonable(getattr(o, f.name)) for f in fields(o)}
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(x) for x in o]
    if isinstance(o, Path):
        return str(o)
    return o


def _json_exit(result) -> int:
    """The same semantic exit code the prose path produces, so an agent can branch on `$?`.

    This guessed from PYTHON CONTAINER TYPES rather than from what the operation found, and the
    guess was wrong in both directions. A `zoom --level full` for a card with no Full section
    returns a populated `CardContent` whose `body` is None: not None, not a `Note`, not an empty
    list — so JSON exited 0 while prose exited 1 for the identical request. Ask each payload
    whether it found anything instead of inferring it from its shape.
    """
    v = result.value
    if isinstance(v, core.EvidenceHit):
        return 0 if v.found else 1
    if v is None or isinstance(v, core.Note):
        return 1
    if isinstance(v, core.CardContent):
        # the requested SECTION is the answer; a card that exists without it is a miss
        return 0 if v.body else 1
    if isinstance(v, core.FanOut):
        # a fan-out that found nothing anywhere is a miss; one that found something is not
        return 0 if any(_json_exit(item) == 0 for item in v.items) else 1
    if isinstance(v, (list, tuple)) and not v:
        # An empty CATALOG is a successful answer to "what is registered?" — prose says so and
        # exits 0. An empty SEARCH RESULT is a miss. Guessing from emptiness alone conflated them,
        # so `--json kbs` exited 1 on a fresh install while `klode kbs` exited 0.
        return 0 if result.op_id in _EMPTY_IS_AN_ANSWER else 1
    return 0


def _emit_json(result) -> int:
    print(json.dumps(_jsonable(result), ensure_ascii=False))
    return _json_exit(result)


def _run(args, op_id: str, params: dict):
    """Execute an op and emit JSON (--json) — returns the OpResult so a caller can also render prose."""
    return services.execute(_pool(args), op_id, getattr(args, "kb", None), params)


# ---- new consumption verbs (routed through execute; prose + --json) ----
def cmd_verify(args) -> int:
    r = _run(args, "verify", {"card": args.card, "phrase": args.phrase})
    if args.json:
        return _emit_json(r)
    e = r.value
    kb = f"[{r.provenance.kb}] " if r.provenance.kb else ""
    print(f"{kb}{e.resolution.value.upper()} — {args.phrase!r}"
          + (f" in {e.rel}" if e.rel else ""))
    for n, ln in e.lines:
        print(f"  {n}: {ln}")
    return 0 if e.found else 1


def cmd_lenses(args) -> int:
    r = _run(args, "lenses.list", {})
    if args.json:
        return _emit_json(r)
    v = r.value
    for d in v["dimensions"]:
        print(f"dimension  {d.name}  [{d.status}]")
    for f in v["frameworks"]:
        print(f"framework  {f.name}  ({f.dimension})")
    return 0


def cmd_cards(args) -> int:
    r = _run(args, "cards.list", {})
    if args.json:
        return _emit_json(r)
    for c in r.value:
        print(f"{c['id']}  — {c['title']}")
    return 0


def _wrap(text: str, width: int) -> list[str]:
    import textwrap
    return textwrap.wrap(text, width) or [""]


def cmd_settings(args) -> int:
    """Print the effective settings and which source won each one. Without this, configuration
    split across argument / environment / file / default is not auditable — a user cannot tell why
    a value is what it is."""
    from . import settings as _settings
    try:
        resolved = _settings.resolve(args)
    except ValueError as e:
        print(f"settings error: {e}", file=sys.stderr)
        return 1
    if getattr(args, "lint", False):
        problems = _settings.lint(home=None)
        for msg in problems:
            print(f"  INVALID  {msg}", file=sys.stderr)
        print(f"\n{'FAIL' if problems else 'OK'}: {len(problems)} invalid value(s) in "
              f"{_settings.settings_path()}")
        return 1 if problems else 0
    show_sources = not getattr(args, "values_only", False)
    print(f"settings file: {_settings.settings_path()}"
          f"{'' if _settings.settings_path().is_file() else '  (absent — not an error)'}")

    if getattr(args, "explain", False):
        # Every Spec carries `help`, `choices`, `env` and a default, and NONE of it was printed
        # anywhere. A setting a user cannot discover is a setting that does not exist for them —
        # `marker_mode` had a paragraph of reasoning reachable only by reading the source.
        print()
        for spec in sorted(_settings.SPEC, key=lambda s: (s.section, s.key)):
            name = f"{spec.section}.{spec.key}"
            st = resolved[name]
            print(f"{name}")
            print(f"  now      {'(unset)' if st.value is None else repr(st.value)}"
                  f"  (from {st.source})")
            print(f"  type     {spec.kind.__name__}"
                  + (f", one of {list(spec.choices)}" if spec.choices else "")
                  + (f", {spec.lo}..{spec.hi}" if spec.lo is not None or spec.hi is not None else "")
                  + (f", starts with {list(spec.prefixes)}" if spec.prefixes else ""))
            print(f"  default  {'(none — unset on purpose)' if spec.default is None else repr(spec.default)}")
            print(f"  env      {spec.env or '(none)'}")
            for line in _wrap(spec.help, 72):
                print(f"  {line}")
            print()
        return 0

    print(f"{'setting':<24} {'value':<24}" + ("  source" if show_sources else ""))
    print("-" * (50 + (10 if show_sources else 0)))
    for name in sorted(resolved):
        st = resolved[name]
        shown = "(unset)" if st.value is None else repr(st.value)
        print(f"{name:<24} {shown:<24}" + (f"  {st.source}" if show_sources else ""))
    if not getattr(args, "values_only", False):
        print("\n`klode settings --explain` describes what each one does.")
    return 0


MAX_DRAFT_CHARS = 4 * 1024 * 1024
"""A draft is a piece of prose someone wrote. `sys.stdin.read()` accepted an unbounded stream, so
a mistaken `klode review - < /dev/zero` (or a large file) exhausted memory before anything was
validated."""


def _read_draft() -> str:
    data = sys.stdin.read(MAX_DRAFT_CHARS + 1)
    if len(data) > MAX_DRAFT_CHARS:
        raise SystemExit(f"draft exceeds {MAX_DRAFT_CHARS // 1048576} MB on stdin — "
                         "review one document at a time")
    return data


def cmd_review(args) -> int:
    draft = _read_draft() if args.draft == "-" else args.draft
    from . import settings as _settings
    try:
        # `judge.hurdle` was declared, printed by `klode settings`, and read by nothing — the
        # service hardcoded 60. A setting that cannot change behaviour is worse than no setting:
        # it invites a change and then silently ignores it.
        resolved = _settings.resolve(args)
        hurdle = resolved.value("judge.hurdle")
    except ValueError as e:
        print(f"settings error: {e}", file=sys.stderr)
        return 1
    judge = None
    if getattr(args, "live_judge", False):
        # A config file is an adequate consent surface for CHOOSING a model. It is not adequate
        # consent for SPENDING: `ANTHROPIC_API_KEY` is commonly ambient for other tools, and this
        # command advertises a stub, so turning the same invocation into billed network calls on
        # the strength of a stored value would break the contract it currently states.
        # `1 + permutations` calls per criterion, so the default is three per criterion.
        from klode import gate
        from klode.gate.llm_judge import anthropic_transport
        model = resolved.value("judge.model")
        if not model:
            print("--live-judge needs a model: set [judge].model or $KLODE_JUDGE_MODEL. It has no "
                  "default on purpose — it must differ from whatever produced the draft "
                  "(self-enhancement bias).", file=sys.stderr)
            return 2
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("--live-judge needs $ANTHROPIC_API_KEY. Keys are environment-only and can never "
                  "be settings.", file=sys.stderr)
            return 2
        perms = resolved.value("judge.permutations")
        print(f"live judge: {model}, {perms} permutation(s) — this makes BILLED API calls "
              f"({1 + perms} per criterion). The verdict remains uncalibrated.", file=sys.stderr)
        judge = gate.LLMJudge(anthropic_transport(model), model=model, permutations=perms)
    try:
        r = _run(args, "review", {"draft": draft, "dimension": args.dimension,
                                  "hurdle": hurdle, "judge": judge})
    except ValueError as e:      # the stub gate raises ValueError when nothing grounds (corpus absent)
        print(f"review unavailable: {e}", file=sys.stderr)
        return 1
    except Exception as e:       # a live judge's transport failure is a message, not a traceback
        from klode.gate import JudgeError
        if not isinstance(e, JudgeError):
            raise
        print(f"judge unavailable: {e}", file=sys.stderr)
        return EXIT_ABSTAINED
    if args.json:
        return _emit_json(r)
    rv = r.value
    print(f"[{rv.capability.value}] judge={rv.judge_identity} non_production={rv.non_production}")
    if rv.decision:
        # the label must track the judge that actually ran, not be hardcoded to the stub
        why = ("uncalibrated — no measured agreement with humans on this rubric"
               if rv.non_production else "calibrated")
        print(f"VERDICT (NOT AUTHORITATIVE — {rv.judge_identity}, {why}): "
              f"{rv.decision}  score {rv.score}/100")
    else:
        print("no verdict — capability unavailable")
    return 0


_CTRL_SAFE = {0x09, 0x0a}
"""Tab and newline only.

Tab is real layout in a source line and newline is how every multi-line message this module prints
is built — stripping it turned `print("\n" + body)` into one run-on line, which the sanitiser's own
test caught. Carriage return is NOT here on purpose: `\r` without `\n` returns the cursor to the
start of the line so the next text overwrites what was printed, which is a spoofing primitive
rather than layout."""


def print(*args, **kwargs):                                            # noqa: A001
    """Every prose line this module writes, with terminal control sequences neutered.

    Shadowing the builtin ON PURPOSE. The first version of this fix sanitised four print sites by
    hand, and immediately missed a fifth — `cmd_verify` printed raw source lines exactly as
    `cmd_zoom` had, the same vector in a different command. Per-site vigilance is what produced
    that gap and would keep producing it for every print added later.

    So the boundary is the module's output function, not a list of call sites. klode's own literals
    contain no control characters, making this a no-op for them; only file-derived text is changed.
    JSON is unaffected — `_emit_json` hands `json.dumps` output through, which is already escaped,
    and a machine consumer wants the real bytes.
    """
    _builtin_print(*(sane(a) if isinstance(a, str) else a for a in args), **kwargs)


def sane(text: str) -> str:
    """Strip terminal control sequences from CORPUS- or CARD-derived text before printing.

    Everything klode prints at L1/L3 comes from files it was pointed at, and a card or a source
    .txt is not trusted input — cards travel through the registry, and a corpus is whatever PDF you
    fed it. A raw `\x1b[2J\x1b[H` clears the reader's screen and rewrites it; `\x1b]0;…\x07` sets the
    window title; other sequences can spoof a prompt or drive a clipboard. These were printed
    verbatim.

    JSON output is untouched: `json.dumps` already escapes them, and a machine consumer wants the
    bytes that are actually in the file.
    """
    return "".join(ch if (ord(ch) >= 0x20 and not (0x7f <= ord(ch) <= 0x9f)) or ord(ch) in _CTRL_SAFE
                   else "\uFFFD" for ch in text)


def _positive_int(raw: str) -> int:
    """A count, validated at PARSE time. `--limit 0` sliced to an empty list and printed "no
    matching cards" — a false negative indistinguishable from a real one — and `--limit -1`
    silently dropped the last result, while the JSON path clamped both to 1. Two surfaces, two
    different wrong answers, from one unvalidated flag."""
    try:
        v = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{raw!r} is not an integer")
    if v < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {raw}")
    return v


def _unit_interval(raw: str) -> float:
    """A probability threshold, validated at PARSE time. `--entail-threshold nan` made every
    `support < threshold` comparison False, so a backend returning 0.0 for every claim scored
    "0 below threshold" and the linter printed OK — a NaN threshold turns the check off while
    leaving it looking on."""
    import math
    try:
        v = float(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{raw!r} is not a number")
    if not math.isfinite(v) or not 0.0 <= v <= 1.0:
        raise argparse.ArgumentTypeError(
            f"must be a finite number in [0, 1], got {raw!r}")
    return v


def _tier_choices() -> tuple[str, ...]:
    """The PDF tier list, from the settings SPEC — the one place it is declared."""
    from . import settings
    return next(s for s in settings.SPEC if (s.section, s.key) == ("ingest", "tier")).choices


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=PROG, description="klode — a grep-grounded, level-of-zoom "
                                                        "knowledge library with a citation-rot linter.")
    src = p.add_mutually_exclusive_group()             # a KB comes from ONE place: a file or the registry
    src.add_argument("-c", "--config", help="path to library.toml (default: nearest in cwd/parents)")
    src.add_argument("--kb", help="address a registered KB by id (via the registry) instead of --config")
    p.add_argument("--registry", help="registry manifest path (for --kb; else default precedence)")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON instead of prose (for agentic consumption). "
                        "Supported by the consumption verbs: " + ", ".join(sorted(JSON_COMMANDS)))
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init", help="scaffold a new library")
    pi.add_argument("dir", nargs="?", default=".", help="target directory (default: cwd)")
    pi.add_argument("--shelf", action="append", help="shelf name (repeatable; default: books, papers)")
    pi.add_argument("--force", action="store_true", help="overwrite an existing library.toml")
    pi.set_defaults(func=cmd_init)

    pb = sub.add_parser("build", help="scaffold/refresh cards + the board")
    pb.add_argument("--stamp", action="store_true",
                    help="(re)compute each installed source's freshness hash — do this after "
                         "re-verifying claims against the current source")
    pb.add_argument("--allow-unmeasured", action="store_true",
                    help="exit 0 even when nothing could be built (e.g. no corpus installed)")
    pb.set_defaults(func=cmd_build)

    pc = sub.add_parser("check", help="integrity + citation-rot linter")
    pc.add_argument("--quiet", action="store_true", help="print only problems + summary")
    pc.add_argument("--strict", action="store_true",
                    help="also WARN when a bare anchor resolves ambiguously (>1 place) — "
                         "pin it with before:/after:/#n")
    pc.add_argument("--entail", action="store_true",
                    help="also run the OPT-IN, warn-only entailment check (needs `klode[entail]`): "
                         "does the source window actually support each claim?")
    pc.add_argument("--entail-model", default=None,
                    help="override the pinned NLI/fact-check model (default: MiniCheck-Flan-T5-Large)")
    pc.add_argument("--entail-threshold", type=_unit_interval, default=0.5,
                    help="WARN when model support for a claim falls below this (default 0.5)")
    pc.add_argument("--allow-unmeasured", action="store_true",
                    help="exit 0 even when a check could not run (e.g. the corpus is not "
                         "installed). Without this, an unmeasured check exits 2 as ABSTAINED — "
                         "it is not a pass, and CI reads the exit code, not the notes")
    pc.set_defaults(func=cmd_check)

    pg = sub.add_parser("ingest", help="any source (PDF/EPUB/DOCX/HTML/TXT) -> clean, grep-ready shelf source")
    pg.add_argument("source", help="path to the source file (format detected by content, not extension)")
    pg.add_argument("--shelf", required=True, help="target shelf (must be a configured shelf)")
    pg.add_argument("--id", help="card/source id (default: slug of the filename)")
    pg.add_argument("--format", choices=["auto", "pdf", "epub", "docx", "html", "txt"], default="auto",
                    help="force a format handler; auto (default) detects by content signature")
    # default=None, NOT "auto": with a value default, `ingest x` and `ingest x --tier auto`
    # produce identical namespaces and the settings precedence chain cannot tell a deliberate
    # choice from silence. The effective default comes from settings.resolve().
    # Choices come from the settings SPEC, not a second hand-maintained list. They had already
    # drifted: `marker` was a valid tier everywhere except here, so `--tier marker` was rejected
    # while `[ingest].tier = "marker"` in settings.toml worked — the same capability reachable
    # from one surface and not the other. `tests/test_settings.py` binds the SPEC to the actual
    # extractor table so a third copy cannot appear.
    pg.add_argument("--tier", choices=list(_tier_choices()), default=None,
                    help="PDF only: auto picks by measured corruption; force a tier to override. "
                         "`marker` and `docling` are remote backends — see [ingest].docling_url / "
                         "marker_url (default: auto, or [ingest].tier from ~/.klode/settings.toml)")
    pg.add_argument("--lang", default="eng", help="OCR language (default eng)")
    pg.add_argument("--verify", action=argparse.BooleanOptionalAction, default=None,
                    help="check extraction integrity against a control before promoting to the "
                         "shelf (default: on)")
    pg.add_argument("--accept-unverified", action="store_true",
                    help="write even when verification FAILS, recording status=unverified — the "
                         "numbers are still persisted")
    pg.add_argument("--force", action="store_true", help="overwrite an existing shelf source")
    pg.set_defaults(func=cmd_ingest)

    pn = sub.add_parser("normalize", help="grep-readiness preprocessor for the corpus")
    pn_mode = pn.add_mutually_exclusive_group()
    pn_mode.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    pn.add_argument("--glob", default="*/*.txt", help="which library files to process")
    pn.add_argument("--stamp", default=None, help="backup dir name (default: derived)")
    # mutually exclusive: together, normalization MUTATED the files and the `--check` gate was
    # silently disabled — the one combination that both changes the corpus and reports nothing
    pn_mode.add_argument("--check", action="store_true",
                         help="dry run that exits nonzero if anything would change")
    pn.add_argument("--allow-unmeasured", action="store_true",
                    help="exit 0 even when --check inspected no file")
    pn.set_defaults(func=cmd_normalize)

    ps = sub.add_parser("search", help="L0/L1 retrieval over the cards")
    ps.add_argument("query", nargs="+", help="one or more terms")
    ps.add_argument("--full", action="store_true", help="also search L2 (Full) bodies")
    ps.add_argument("--limit", type=_positive_int, default=20, help="max results (default 20)")
    ps.set_defaults(func=cmd_search)

    pz = sub.add_parser("zoom", help="pull one level of a card")
    pz.add_argument("id", help="card id (filename stem)")
    pz.add_argument("--level", choices=["meta", "thin", "full", "content"], default="thin")
    pz.add_argument("--grep", help="with --level content: verify a phrase against the source .txt")
    # default None, not 10: an explicit `--max 10` was indistinguishable from omission, so the
    # dependency check could not tell "asked for and ignored" from "not asked for"
    pz.add_argument("--max", type=_positive_int, default=None,
                    help="max matching lines to show (default 10; needs --grep)")
    pz.set_defaults(func=cmd_zoom)

    pco = sub.add_parser("consult", help="read a craft lens — by dimension, thinker, author, or book title")
    pco.add_argument("name", nargs="?",
                     help="a dimension, thinker, author name, or book title (omit to list every lens)")
    pco.add_argument("--section", help="show only sections matching this keyword (e.g. spec, options, engine)")
    pco.add_argument("--full", action="store_true", help="show the whole lens, not the default view")
    pco.set_defaults(func=cmd_consult)

    pd = sub.add_parser("diagnose", help='route a symptom ("the reveal thudded") to the dimensions to consult')
    pd.add_argument("symptom", nargs="+", help="what feels wrong, in your own words")
    pd.set_defaults(func=cmd_diagnose)

    pk = sub.add_parser("kbs", help="list the KBs registered in a manifest (id + description)")
    pk.add_argument("--registry", default=argparse.SUPPRESS,   # don't clobber the global --registry
                    help="path to a registry manifest (else ./.klode/registry.toml, then ~/.klode/registry.toml)")
    pk.set_defaults(func=cmd_kbs)

    pv = sub.add_parser("verify", help="check a phrase against a card's source (the grounding verb)")
    pv.add_argument("card", help="card id")
    pv.add_argument("phrase", help="the exact phrase to check")
    pv.set_defaults(func=cmd_verify)

    pl = sub.add_parser("lenses", help="list the craft dimensions and frameworks")
    pl.set_defaults(func=cmd_lenses)

    pcd = sub.add_parser("cards", help="list the source cards")
    pcd.set_defaults(func=cmd_cards)

    pset = sub.add_parser("settings", help="show effective settings and where each value came from")
    # one mode, not two overlapping flags: `--effective` used to HIDE sources despite its help
    # saying effective values were the default, and `--sources` was a no-op unless it did.
    pset.add_argument("--values-only", action="store_true",
                      help="print only the effective values, omitting where each came from")
    pset.add_argument("--lint", action="store_true",
                      help="validate EVERY value in the settings file, including ones an "
                           "override shadows — a shadowed broken value goes live the moment the "
                           "override is removed")
    pset.add_argument("--explain", action="store_true",
                      help="describe every setting: what it does, its allowed values, its "
                           "environment variable, and its built-in default")
    pset.set_defaults(func=cmd_settings)

    prv = sub.add_parser("review", help="[experimental] supervise a draft against a dimension (stub judge)")
    prv.add_argument("draft", help="the draft text, or - to read stdin")
    prv.add_argument("dimension", help="the craft dimension to review against")
    prv.add_argument("--live-judge", action="store_true",
                     help="use the real rubric judge from [judge].model instead of the fixture "
                          "judge. Makes BILLED API calls (1 + permutations per criterion) and "
                          "needs $ANTHROPIC_API_KEY. Without this flag klode makes no network "
                          "call, whatever the settings say")
    prv.set_defaults(func=cmd_review)
    return p


_EMPTY_IS_AN_ANSWER = frozenset({"kbs.list", "cards.list", "lenses.list"})
"""Ops whose empty result is a complete, successful answer rather than a failed lookup.

"No KBs are registered" is the true state of a fresh install; "no card matched" is a miss. Both are
an empty list, so shape cannot distinguish them and the op must."""

JSON_COMMANDS = frozenset({"search", "zoom", "consult", "diagnose", "verify", "cards",
                           "lenses", "kbs", "review"})
"""Commands whose `--json` is actually implemented.

The flag is global, so it parsed everywhere and was honoured by roughly half. `klode --json check`
printed prose and exited 0, which a machine consumer reads as valid, non-JSON output — the worst
of the three possible behaviours (emit JSON / refuse / silently emit prose)."""


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "json", False) and args.cmd not in JSON_COMMANDS:
        print(f"--json is not implemented for `{args.cmd}` (supported: "
              f"{', '.join(sorted(JSON_COMMANDS))}) — it would have printed prose",
              file=sys.stderr)
        return 2
    try:
        if getattr(args, "kb", None) == "*" and not getattr(args, "json", False):
            raise ConfigError("`--kb '*'` (fan out over all KBs) needs --json; prose renders one KB")
        return args.func(args)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
