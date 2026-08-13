"""Integrity + citation-rot linter for a library. READ-ONLY and side-effect-free.

The library's whole design rests on two disciplines that are usually documented but never
enforced: every source has a card, and every L1/L2 claim is *grep-grounded* to the source
.txt ("cite, don't recall"). Nothing checked either, so drift crept in silently. This is the
guard — the single most valuable piece to port.

Checks (ERROR fails the run; WARN is reported but does not fail):

  A. card -> source          every card front-matter `file:` points to a real .txt        [ERROR]
  B. source -> card          every shelf .txt has a card                                  [ERROR]
  C. bibliography coverage   every shelf .txt is mentioned in the bibliography            [WARN]
  G. bibliography mirror     every card's embedded `**Bibliography.**` line matches live   [WARN]
  D. INDEX freshness         cards/INDEX.md lists exactly the cards on disk               [ERROR]
  E. copyright leak guard    no guarded .txt/.pdf is git-tracked                          [ERROR]
  F. citation rot            every grep marker still occurs in its source .txt            [ERROR]

The F matcher NORMALIZES before comparing — collapses whitespace (so a phrase wrapped across
pdftotext lines still matches) and tolerates hyphenation both ways — the exact failure this
guard exists to catch. The framework/synthesis checks run only when that layer is enabled.
The corpus is typically git-ignored (copyrighted): when it is absent (a fresh clone), the
card->source and rot checks are skipped rather than false-failing every commit.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date

from .common import (NON_CARDS, card_files, front_matter, fm_get, glob_in, haystacks,
                     parse_markers, read, read_lenient, resolve, shelf_txts, src_path_re)
from .config import Config


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warns: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    n_cards: int = 0
    n_txts: int = 0
    unmeasured: list[str] = field(default_factory=list)
    """Checks that could NOT run. Not warnings and not errors — a third fact.

    Without this, `check` printed `OK: 0 errors` and exited 0 on a library whose corpus was
    absent, meaning citation-rot (the guard this whole tool exists to provide) never executed.
    Every citation could have rotted and CI, which reads the exit code and not the notes, would
    have recorded a pass. That is the same `abstained`-read-as-`verified` defect the extraction
    integrity work exists to refuse, sitting in the primary gate.
    """

    @property
    def ok(self) -> bool:
        """No errors AND nothing silently skipped. `abstained` is not `ok` — see `klode.lib.integrity`,
        which draws the identical line for extraction."""
        return not self.errors and not self.unmeasured

    @property
    def abstained(self) -> bool:
        return bool(self.unmeasured) and not self.errors


def _check_enumerator_agrees_with_disk(cfg: Config, r: Report) -> None:
    """The assertion the glob-escaping fix leaves behind.

    A path metacharacter in the library directory made `card_files` return `[]` for a shelf full of
    cards, and because `unmeasured` is only recorded `if cards:`, `check` reported `OK: 0 errors`
    and exited 0 over a rotted citation. The fix removes today's cause; this stops any future cause
    from being silent, because an enumerator that disagrees with the directory is a bug, never an
    empty library.

    Compared against the SAME exclusion set `card_files` uses — a directory holding only `INDEX.md`
    and `README.md` genuinely has no cards and must not fire. An unreadable directory is reported
    as its own error rather than raised: this is a linter, and it does not get to crash.
    """
    if not cfg.cards.is_dir():
        return
    try:
        on_disk = [e.name for e in os.scandir(cfg.cards)
                   if e.is_file() and e.name.endswith(".md") and e.name not in NON_CARDS]
    except OSError as e:
        r.errors.append(f"[A] cannot read the cards directory {cfg.cards}: {e}")
        return
    if on_disk and not r.n_cards:
        r.errors.append(
            f"[A] card enumeration returned nothing while {len(on_disk)} card file(s) sit in "
            f"{cfg.cards} ({', '.join(sorted(on_disk)[:3])}…) — the enumerator disagrees with the "
            "directory, so NO citation was checked. This is a bug in klode, not an empty library.")


def check(cfg: Config, *, strict: bool = False, entail=None, entail_threshold: float = 0.5,
          today: date | None = None) -> Report:
    r = Report()
    r.n_cards = len(card_files(cfg))
    r.n_txts = len(shelf_txts(cfg))
    _check_enumerator_agrees_with_disk(cfg, r)
    _check_orphans_and_rot(cfg, r, strict)
    if cfg.bib_enabled:
        _check_bibliography(cfg, r)
        _check_bibliography_mirror(cfg, r)
    _check_index_fresh(cfg, r)
    _check_freshness(cfg, r, today or date.today())
    if cfg.copyright_guard:
        _check_copyright_leak(cfg, r)
    if entail is not None:
        _check_entailment(cfg, r, entail, entail_threshold)
    return r


def _check_freshness(cfg: Config, r: Report, today: date) -> None:
    """WARN — freshness is distinct from citation rot. A source can change (re-OCR, new edition)
    while every anchor still resolves, silently invalidating claims written against the old text;
    a review date can lapse; a source can be superseded. All three are review prompts, not gates."""
    SRC_RE = src_path_re(cfg)
    for p in card_files(cfg):
        fm = front_matter(read(p))
        cid = os.path.basename(p)[:-3]
        if sup := fm_get(fm, "superseded_by"):
            r.warns.append(f"[H superseded] {cid}: superseded_by `{sup.strip()}` — re-point references / retire")
        if rb := fm_get(fm, "review_by"):
            try:
                if date.fromisoformat(rb.strip()) < today:
                    r.warns.append(f"[H stale] {cid}: review_by {rb.strip()} has passed — re-review")
            except ValueError:
                r.warns.append(f"[H] {cid}: review_by `{rb.strip()}` is not an ISO date (YYYY-MM-DD)")
        stored = fm_get(fm, "source_sha256")
        rel = (fm_get(fm, "file") or "").strip()
        src = os.path.join(cfg.root, rel)
        # same path discipline as the rot check: only a valid, in-tree shelf source is opened
        if stored and rel and SRC_RE.fullmatch(rel) \
                and os.path.realpath(src).startswith(os.path.realpath(cfg.lib) + os.sep) \
                and os.path.exists(src):     # git-ignored corpus absent → skip, like the rot check
            with open(src, "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() != stored.strip():
                    r.warns.append(f"[H source-changed] {cid}: source changed since it was stamped "
                                   f"(claims may need re-verifying) — re-check, then `klode build --stamp`")


def _check_entailment(cfg: Config, r: Report, backend, threshold: float) -> None:
    """OPT-IN, WARN-ONLY. For each literal anchor whose source is installed, score the window
    the anchor resolves in (premise) against the card's claim (SummaC granularity). A low score
    is a second opinion to review — never a failure; grep resolution remains the only gate."""
    from . import entail as entail_mod
    SRC_RE = src_path_re(cfg)
    failed_cards: list[str] = []
    scored = low = 0
    for p in card_files(cfg):
        text = read(p)
        rel = (fm_get(front_matter(text), "file") or "").strip()
        abspath = os.path.join(cfg.root, rel)
        # same path discipline as the rot/freshness checks: only a valid, in-tree shelf source is read
        if not (rel and SRC_RE.fullmatch(rel)
                and os.path.realpath(abspath).startswith(os.path.realpath(cfg.lib) + os.sep)
                and os.path.exists(abspath)):
            continue
        cid = os.path.basename(p)[:-3]
        try:
            scores = entail_mod.score_card(text, read_lenient(abspath), backend, cid)
        except Exception as e:      # a per-anchor model failure must not crash the whole check
            r.warns.append(f"[entail] scoring failed for {cid} ({e}) — skipped")
            failed_cards.append(cid)
            continue
        for es in scores:
            scored += 1
            if es.low(threshold):
                low += 1
                r.warns.append(f"[entail {es.support:.2f}<{threshold:g}] {cid}: source window may not "
                               f"support claim — “{es.claim[:90]}” (anchor `{es.phrase}`)")
    r.notes.append(f"[entail] scored {scored} anchor(s) with {backend.name}; {low} below support "
                   f"threshold {threshold:g} (advisory — grep remains the gate)")
    if failed_cards:
        r.unmeasured.append(f"--entail could not score {len(failed_cards)} card(s): "
                            f"{', '.join(sorted(failed_cards)[:5])}")


def _check_orphans_and_rot(cfg: Config, r: Report, strict: bool = False) -> None:
    SRC_RE = src_path_re(cfg)
    skipped_cards: list[str] = []
    cards = card_files(cfg)
    card_stems = {os.path.basename(p)[:-3] for p in cards}
    txts = shelf_txts(cfg)

    if not txts:
        r.notes.append("corpus not installed (0 shelf sources present) — skipped card→source (A/B) "
                       "and citation-rot (F) checks; tracked-file checks (D/E) still enforced")
        if cards:
            # Only UNMEASURED when there was something to measure. A library with no cards and no
            # sources has nothing to check and legitimately passes; a library with cards whose
            # anchors were never resolved has not been checked at all.
            r.unmeasured.append(
                f"citation-rot and card→source were NOT checked for {len(cards)} card(s): the "
                "corpus is not installed. Anchors may have rotted without this run being able to "
                "tell. Install the corpus, or pass --allow-unmeasured to accept the gap knowingly.")
        _blanket = True
    else:
        _blanket = False

    # B. source -> card
    for t in txts:
        stem = os.path.splitext(os.path.basename(t))[0]
        if stem not in card_stems:
            r.errors.append(f"[B orphan] shelf source has no card: {os.path.relpath(t, cfg.root)} "
                            f"-> run `klode build`")

    # A. card -> source  (+ cache haystacks for F)
    hay_cache: dict[str, tuple[str, str]] = {}
    for p in cards:
        text = read(p)
        rel = fm_get(front_matter(text), "file")
        if not rel:
            r.errors.append(f"[A] card has no front-matter `file:` (source + citation checks bypassed): "
                            f"{os.path.relpath(p, cfg.root)}")
            continue
        rel = rel.strip()
        abspath = os.path.join(cfg.root, rel)
        if not (SRC_RE.fullmatch(rel)
                and os.path.realpath(abspath).startswith(os.path.realpath(cfg.lib) + os.sep)):
            r.errors.append(f"[A] card {os.path.basename(p)} has an invalid source path "
                            f"(must be {cfg.lib_rel}/<shelf>/<name>.txt): {rel}")
            continue
        if not os.path.exists(abspath):
            # git-ignored corpus: an absent source almost always means 'not installed here',
            # not 'deleted' — skip this card individually rather than false-fail a partial corpus.
            # But skipping it means its anchors were NOT checked, and a PARTIAL corpus reported
            # `OK: 0 errors` exit 0 while some cards went unverified. The blanket "0 sources" case
            # was fixed first and this one survived it: half-measured is still not measured.
            r.notes.append(f"[A] source not installed, card skipped: {rel}")
            skipped_cards.append(rel)
            continue
        # F. citation rot — this card's grep markers against its own source
        markers = parse_markers(text)
        if markers:
            if abspath not in hay_cache:
                hay_cache[abspath] = haystacks(read_lenient(abspath))
            hays = hay_cache[abspath]
            for mk in markers:
                res = resolve(mk, hays)
                if not res.found:
                    r.errors.append(f"[F citation-rot] {os.path.basename(p)}: grep `{mk.phrase}` no longer in {rel}")
                elif strict and res.ambiguous:
                    r.warns.append(f"[F-ambiguous] {os.path.basename(p)}: grep `{mk.phrase}` resolves in "
                                   f"{res.count} places in {rel} — add before:/after:/#n to pin it")

    if cfg.fw_enabled:
        _check_frameworks(cfg, r, SRC_RE, hay_cache, cards, bool(txts))

    if skipped_cards and not _blanket:
        # PARTIAL corpus: the blanket case above only fires when NOTHING is installed. Half the
        # sources present still means these cards' anchors were never resolved, and the run
        # previously ended in `OK: 0 errors` exit 0.
        r.unmeasured.append(
            f"citation-rot was NOT checked for {len(skipped_cards)} card(s) whose source is not "
            f"installed: {', '.join(sorted(skipped_cards)[:5])}"
            + (" …" if len(skipped_cards) > 5 else "")
            + ". Install them, or pass --allow-unmeasured to accept the gap knowingly.")


def _check_frameworks(cfg, r, SRC_RE, hay_cache, cards, corpus_present):
    # F (frameworks). each framework card's grep markers vs every source .txt it references.
    for p in sorted(glob_in(cfg.frameworks, "*.md")):
        if os.path.basename(p) == "README.md":
            continue
        text = read(p)
        markers = parse_markers(text)
        if not markers:
            continue
        srcs = [os.path.join(cfg.root, s) for s in set(SRC_RE.findall(text))]
        srcs = [s for s in srcs if os.path.exists(s)]
        if not srcs:
            r.warns.append(f"[F] framework card has grep markers but no resolvable source path: "
                           f"{os.path.relpath(p, cfg.root)}")
            continue
        hays = []
        for s in srcs:
            if s not in hay_cache:
                hay_cache[s] = haystacks(read_lenient(s))
            hays.append(hay_cache[s])
        for mk in markers:
            if not any(resolve(mk, h).found for h in hays):
                r.errors.append(f"[F citation-rot] {os.path.relpath(p, cfg.lib)}: grep `{mk.phrase}` not found in "
                                f"any referenced source ({', '.join(os.path.basename(s) for s in srcs)})")

    # F (syntheses). _syntheses/*.md adjudicate across cards; resolve sources via `cards:` front-matter.
    # WARN, not ERROR: syntheses may quote an unlisted cross-ref source.
    if corpus_present and cfg.syntheses and os.path.isdir(cfg.syntheses):
        # Resolve each cited card-name to its source text(s). Regular L2 cards carry
        # their source in front-matter `file:`; framework cards declare it inline as
        # **Source:** `library/…` — the same form the D-check above resolves. Both feed
        # the map, else a synthesis that cites only framework cards resolves to nothing
        # and its grep anchors go unchecked (citation rot slips through as a soft warn).
        card_src: dict[str, list[str]] = {}
        for cp in cards:
            crel = fm_get(front_matter(read(cp)), "file")
            if crel:
                card_src.setdefault(os.path.basename(cp)[:-3], []).append(os.path.join(cfg.root, crel))
        for cp in glob_in(cfg.frameworks, "*.md"):
            stem = os.path.basename(cp)[:-3]
            for s in set(SRC_RE.findall(read(cp))):
                card_src.setdefault(stem, []).append(os.path.join(cfg.root, s))
        for p in sorted(glob_in(cfg.syntheses, "*.md")):
            if os.path.basename(p) in ("README.md", "GATE-TRIAGE.md"):
                continue
            text = read(p)
            markers = parse_markers(text)
            if not markers:
                continue
            fm = front_matter(text)
            names = [n for n in re.findall(r"[A-Za-z0-9][A-Za-z0-9-]{2,}", fm_get(fm, "cards") or "")
                     if n not in ("cross", "ref")]
            srcs = []
            for n in names:
                for stem, slist in card_src.items():
                    if stem == n or stem.startswith(n + "-") or n in stem:
                        srcs += [s for s in slist if os.path.exists(s)]
            srcs += [os.path.join(cfg.root, s) for s in set(SRC_RE.findall(text))
                     if os.path.exists(os.path.join(cfg.root, s))]
            srcs = sorted(set(srcs))
            if not srcs:
                r.warns.append(f"[F] synthesis has grep markers but no resolvable source (check `cards:`): "
                               f"{os.path.relpath(p, cfg.lib)}")
                continue
            hays = []
            for s in srcs:
                if s not in hay_cache:
                    hay_cache[s] = haystacks(read_lenient(s))
                hays.append(hay_cache[s])
            for mk in markers:
                if not any(resolve(mk, h).found for h in hays):
                    r.warns.append(f"[F citation-rot?] {os.path.relpath(p, cfg.lib)}: grep `{mk.phrase}` not found "
                                   f"in cited sources — verify (possible rot, or a cross-ref not in `cards:`)")


def _check_bibliography(cfg: Config, r: Report) -> None:
    """The catalog lists sources freely — by filename (`foo.txt`) OR by citation. Match on the
    filename first, then fall back to the stem's author tokens; only warn when NOTHING appears."""
    if not (cfg.bib and cfg.bib.exists()):
        r.errors.append(f"[C] bibliography missing: {cfg.bib}")
        return
    bib = read(cfg.bib)
    bib_l = bib.lower()
    for t in shelf_txts(cfg):
        stem = os.path.splitext(os.path.basename(t))[0]
        # boundary-anchored so `plato` is not counted as covered by a `plato-republic` row
        if f"{stem}.txt" in bib or re.search(rf"`{re.escape(stem)}(?=[.`])", bib):
            continue
        tokens = [tok for tok in stem.lower().split("-") if tok.isalpha() and len(tok) >= 3]
        if tokens and any(tok in bib_l for tok in tokens[:3]):
            continue
        r.warns.append(f"[C] shelf source not found in bibliography (no filename or author token): {stem}")


def _check_bibliography_mirror(cfg: Config, r: Report) -> None:
    """Every card embeds a copy of its bibliography row. Nothing keeps the copy in sync: edit a
    catalog row without rebuilding and the two silently disagree. Reuse the builder's OWN extractor
    so this guard cannot itself drift from the code it checks. WARN — the fix is mechanical."""
    from .build import bib_line_for
    bib_re = re.compile(r"^\*\*Bibliography\.\*\* (.+)$", re.M)
    for path in card_files(cfg):
        stem = os.path.basename(path)[:-3]
        m = bib_re.search(read(path))
        if not m:
            continue                                  # not every card carries the mirror line
        live = bib_line_for(cfg, stem)
        if live is None:
            continue                                  # missing coverage is check C's job
        if m.group(1).strip() != live.strip():
            r.warns.append(f"[G] card '{stem}' Bibliography line is stale vs the catalog -> run `klode build`")


def _check_index_fresh(cfg: Config, r: Report) -> None:
    index = os.path.join(cfg.cards, "INDEX.md")
    if not os.path.exists(index):
        r.errors.append("[D] cards/INDEX.md missing -> run `klode build`")
        return
    idx = read(index)
    listed = set(re.findall(r"\[([^\]]+)\]\((?:\1)\.md\)", idx))
    on_disk = {os.path.basename(p)[:-3] for p in card_files(cfg)}
    missing = on_disk - listed
    extra = listed - on_disk
    if missing:
        r.errors.append(f"[D] INDEX.md is stale — {len(missing)} card(s) on disk not listed "
                        f"({', '.join(sorted(missing)[:5])}{'…' if len(missing) > 5 else ''}) -> run `klode build`")
    if extra:
        r.errors.append(f"[D] INDEX.md lists {len(extra)} card(s) that no longer exist "
                        f"({', '.join(sorted(extra)[:5])}) -> run `klode build`")


def _check_copyright_leak(cfg: Config, r: Report) -> None:
    """Guarded shelf .txt/.pdf must never be git-tracked. The guard must not fail OPEN: inside a git
    repo a failing `git ls-files` is an ERROR; outside a repo there is no git to leak into, so the
    guard is N/A and only noted."""
    try:
        inside = subprocess.run(["git", "-C", str(cfg.root), "rev-parse", "--is-inside-work-tree"],
                                capture_output=True, text=True)
    except FileNotFoundError:
        r.notes.append("[E] git not found on PATH — copyright-leak guard N/A (nothing to leak into)")
        return
    out = inside.stdout.strip()
    # "not a git repository" (rc=128) is a genuine N/A; a DIFFERENT git error (e.g. dubious ownership,
    # also rc=128) must NOT be silently skipped — the leak guard fails CLOSED on it. Distinguish by stderr.
    not_a_repo = (inside.returncode == 0 and out in ("false", "")) \
        or (inside.returncode != 0 and "not a git repository" in inside.stderr.lower())
    if not (inside.returncode == 0 and out == "true"):
        if not_a_repo:
            r.notes.append("[E] not a git work tree — copyright-leak guard N/A (nothing to leak into)")
        else:
            r.errors.append("[E] could not determine git work tree "
                            f"({inside.stderr.strip()[:120] or 'rc=' + str(inside.returncode)}); "
                            "the copyright-leak guard must not fail open")
        return
    try:
        tracked = subprocess.run(
            # -z: no C-quoting of non-ASCII names.
            # --literal-pathspecs: a shelf name is a NAME, not a pattern. It is a MAIN git option
            #   and must precede the subcommand — after it, `ls-files` exits 129 and the guard
            #   errors on every run. Measured effect: with a shelf literally named `books*`, the
            #   pathspec also matched a sibling `booksOTHER/`, so unrelated tracked files were
            #   reported as copyright leaks. A guard that cries wolf gets ignored, which is how it
            #   eventually fails open in practice.
            # --: everything after this is a path, never an option. Config validation rejects only
            #   `/`, `""`, `.` and `..`, so `shelves = ["--others"]` loads; with `[library].dir`
            #   empty the relpath IS `--others`, git consumed it as an OPTION, and the guard
            #   listed untracked files instead of the tracked corpus it exists to catch — rc 0,
            #   no leak reported, a copyrighted source sitting in the index.
            ["git", "-C", str(cfg.root), "--literal-pathspecs", "ls-files", "-z", "--",
             *cfg.guard_relpaths],
            capture_output=True, text=True, check=True).stdout.split("\0")
    except Exception as e:
        r.errors.append(f"[E] could not run git ls-files ({e}); the copyright-leak guard must not fail open")
        return
    for f in (f for f in tracked if f and f.lower().endswith((".txt", ".pdf"))):   # case-insensitive: .TXT must not slip
        r.errors.append(f"[E copyright-leak] corpus file is git-tracked (must be ignored): {f}")
    for d in glob_in(cfg.lib, ".normalize-backup-*"):
        r.errors.append(f"[E] in-tree normalize backup present (should live outside the repo): "
                        f"{os.path.relpath(d, cfg.root)}")
