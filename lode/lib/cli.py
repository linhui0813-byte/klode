"""`lib` — the command-line entry point for an lodlib library.

  lib init        scaffold a new library (library.toml + dirs + .gitignore)
  lib build       scaffold/refresh cards + the board (idempotent)
  lib check       integrity + citation-rot linter (exit 1 on any ERROR)
  lib normalize   grep-readiness preprocessor for the corpus .txt files
  lib search      L0/L1 retrieval — "which sources are relevant?"
  lib zoom        pull one level of a card — meta | thin | full | content
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import console, query
from .config import Config, ConfigError

# ---------------------------------------------------------------------------
# init — scaffold a fresh library
# ---------------------------------------------------------------------------
_TOML_TEMPLATE = """\
# library.toml — an lodlib knowledge library.
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
guard = true              # fail `lib check` if any guarded .txt/.pdf is git-tracked
extra_guard_dirs = []     # extra dirs (relative to the library dir) to also guard

[normalize]
backup_dir = ""           # empty => system temp dir (kept OUTSIDE the tree)
backup_keep = 3
dict_path = "/usr/share/dict/words"
"""

_GITIGNORE_BLOCK = "# lodlib: the corpus is copyrighted — sources stay local, cards are tracked\n{lines}\n"


def cmd_init(args) -> int:
    root = Path(args.dir).resolve()
    cfg_path = root / "library.toml"
    if cfg_path.exists() and not args.force:
        print(f"refusing to overwrite existing {cfg_path} (use --force)", file=sys.stderr)
        return 1
    shelves = args.shelf or ["books", "papers"]
    root.mkdir(parents=True, exist_ok=True)
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
    block = _GITIGNORE_BLOCK.format(lines=ignore_lines)
    if gi.exists():
        if "lodlib: the corpus is copyrighted" not in gi.read_text(encoding="utf-8"):
            gi.write_text(gi.read_text(encoding="utf-8").rstrip() + "\n\n" + block, encoding="utf-8")
    else:
        gi.write_text(block, encoding="utf-8")

    print(f"scaffolded library at {root}")
    print(f"  config : {cfg_path.relative_to(root)}")
    print(f"  shelves: {', '.join(shelves)}")
    print("next: drop a .txt on a shelf, add a BIBLIOGRAPHY row, then `lib build`")
    return 0


# ---------------------------------------------------------------------------
# build / check / normalize — thin wrappers over the modules
# ---------------------------------------------------------------------------
def cmd_build(args) -> int:
    from . import build as build_mod
    cfg = _load(args)
    st = build_mod.build(cfg, stamp=getattr(args, "stamp", False))
    if st.get("skipped"):
        print(st["skipped"])
        return 0
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
    cfg = _load(args)
    backend = None
    if getattr(args, "entail", False):
        from . import entail as entail_mod
        try:
            backend = entail_mod.load_backend(args.entail_model or entail_mod.DEFAULT_MODEL)
        except entail_mod.EntailUnavailable as e:
            print(f"  NOTE  entail skipped — {e}")
    r = check_mod.check(cfg, strict=getattr(args, "strict", False), entail=backend,
                        entail_threshold=getattr(args, "entail_threshold", 0.5))
    if not args.quiet:
        print(f"library check — {r.n_cards} cards, {r.n_txts} shelf sources")
    for nt in r.notes:
        print(f"  NOTE  {nt}")
    for w in r.warns:
        print(f"  WARN  {w}")
    for e in r.errors:
        print(f"  ERROR {e}")
    if r.errors:
        print(f"\nFAIL: {len(r.errors)} error(s), {len(r.warns)} warning(s)")
        return 1
    print(f"\nOK: 0 errors, {len(r.warns)} warning(s)")
    return 0


def cmd_ingest(args) -> int:
    from . import ingest as ingest_mod       # lazy: OCR deps aren't needed to import `lib`
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
    return 0


# ---------------------------------------------------------------------------
# search / zoom — the LOD retrieval surface
# ---------------------------------------------------------------------------
def cmd_search(args) -> int:
    cfg = _load(args)
    if not [t for t in args.query if t.strip()]:
        print("nothing to search for", file=sys.stderr)
        return 1
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


def cmd_zoom(args) -> int:
    cfg = _load(args)
    if not query.card_path(cfg, args.id):
        print(f"no card: {args.id} (try `lib search`)", file=sys.stderr)
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
        print(f"source not installed locally: {src.rel}\n"
              f"(the corpus is git-ignored — install it to grep L3)")
        return 0
    if not args.grep:
        print(f"{src.rel} — {src.size} bytes, grep-ready. "
              f"Pass --grep \"phrase\" to verify a claim against it.")
        return 0
    v = query.verify(cfg, args.id, args.grep, max_lines=args.max)
    if v.found:
        for n, ln in v.lines:      # raw lines, so the reader sees the real source
            print(f"{n}: {ln}")
        if v.folded_only:
            print(f"✓ resolves in {v.rel} (matches across line/hyphenation folding "
                  f"— not on a single line)")
        return 0
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
    print("\nread one:  lib consult <name> [--section spec] [--full]")
    print("stuck?     lib diagnose \"what feels wrong\"")
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
    print(f"\nverify against the source:  lib zoom {cid} --level content --grep \"…\"")
    return 0


def cmd_consult(args) -> int:
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
    from . import registry                        # lazy: registry is only needed for this subcommand
    entries = registry.load(args.registry)        # RegistryError (a ConfigError) → main() exit 2
    if not entries:
        print("no KBs registered — add one to a registry manifest "
              "(--registry PATH, ./.lode/registry.toml, or ~/.lode/registry.toml).")
        return 0
    infos = registry.catalog(entries)
    width = max(len(i.id) for i in infos)
    for i in infos:
        detail = (i.description or "(no description)") if i.ok else f"(unavailable: {i.error})"
        print(f"  {i.id:<{width}}  {detail}")
    return 0


def cmd_diagnose(args) -> int:
    cfg = _load(args)
    symptom = " ".join(args.symptom)
    result = query.diagnose(cfg, symptom)               # symptom -> [(dimension, core_question)]; logic in query
    if result is None:
        loc = f"{cfg.syntheses}/_diagnostics.md" if cfg.syntheses else "the syntheses dir"
        print(f"no diagnostics map — create {loc} (a `| symptom cues | dimensions |` table).", file=sys.stderr)
        return 1
    if not result:
        print("no symptom matched. Browse with `lib consult`, or `lib search <terms>`.", file=sys.stderr)
        return 1
    print(f"“{symptom.lower()}” → consult these panels (most-relevant first):\n")
    for dn, q in result:
        print(f"  • {dn}")
        if q:
            print(f"      {q[:88]}")
        print(f"      → lib consult {dn}")              # §5: the writer default IS the Craft layer — no `--section spec`
    return 0


# ---------------------------------------------------------------------------
def _load(args) -> Config:
    return Config.load(Path(args.config) if args.config else None)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lib", description="lodlib — a grep-grounded, level-of-zoom "
                                                        "knowledge library with a citation-rot linter.")
    p.add_argument("-c", "--config", help="path to library.toml (default: nearest in cwd/parents)")
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
    pb.set_defaults(func=cmd_build)

    pc = sub.add_parser("check", help="integrity + citation-rot linter")
    pc.add_argument("--quiet", action="store_true", help="print only problems + summary")
    pc.add_argument("--strict", action="store_true",
                    help="also WARN when a bare anchor resolves ambiguously (>1 place) — "
                         "pin it with before:/after:/#n")
    pc.add_argument("--entail", action="store_true",
                    help="also run the OPT-IN, warn-only entailment check (needs `lodlib[entail]`): "
                         "does the source window actually support each claim?")
    pc.add_argument("--entail-model", default=None,
                    help="override the pinned NLI/fact-check model (default: MiniCheck-Flan-T5-Large)")
    pc.add_argument("--entail-threshold", type=float, default=0.5,
                    help="WARN when model support for a claim falls below this (default 0.5)")
    pc.set_defaults(func=cmd_check)

    pg = sub.add_parser("ingest", help="tiered PDF -> clean source (pdftotext/xberg/docling, auto-select)")
    pg.add_argument("pdf", help="path to the source PDF")
    pg.add_argument("--shelf", required=True, help="target shelf (must be a configured shelf)")
    pg.add_argument("--id", help="card/source id (default: slug of the filename)")
    pg.add_argument("--tier", choices=["auto", "pdftotext", "xberg", "docling"], default="auto",
                    help="auto picks by measured corruption; force a tier to override")
    pg.add_argument("--lang", default="eng", help="OCR language (default eng)")
    pg.add_argument("--force", action="store_true", help="overwrite an existing shelf source")
    pg.set_defaults(func=cmd_ingest)

    pn = sub.add_parser("normalize", help="grep-readiness preprocessor for the corpus")
    pn.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    pn.add_argument("--glob", default="*/*.txt", help="which library files to process")
    pn.add_argument("--stamp", default=None, help="backup dir name (default: derived)")
    pn.add_argument("--check", action="store_true", help="dry run that exits nonzero if anything would change")
    pn.set_defaults(func=cmd_normalize)

    ps = sub.add_parser("search", help="L0/L1 retrieval over the cards")
    ps.add_argument("query", nargs="+", help="one or more terms")
    ps.add_argument("--full", action="store_true", help="also search L2 (Full) bodies")
    ps.add_argument("--limit", type=int, default=20, help="max results (default 20)")
    ps.set_defaults(func=cmd_search)

    pz = sub.add_parser("zoom", help="pull one level of a card")
    pz.add_argument("id", help="card id (filename stem)")
    pz.add_argument("--level", choices=["meta", "thin", "full", "content"], default="thin")
    pz.add_argument("--grep", help="with --level content: verify a phrase against the source .txt")
    pz.add_argument("--max", type=int, default=10, help="max matching lines to show (default 10)")
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
    pk.add_argument("--registry", help="path to a registry manifest "
                                       "(else ./.lode/registry.toml, then ~/.lode/registry.toml)")
    pk.set_defaults(func=cmd_kbs)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
