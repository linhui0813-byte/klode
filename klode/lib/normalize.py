"""Normalize library .txt files for grep-readiness.

Library texts are typically pdftotext / ebook-convert dumps. Several silently break grep in
four distinct ways; this repairs each with a deterministic, validated transform (never a blind
guess), and is idempotent (a no-op on clean files):

  1. Unicode ligature fold  — U+FB00..FB06 (ﬀﬁﬂﬃﬄﬅﬆ) -> ascii. Lossless.
  2. Control-byte repair     — a raw control byte where a ligature belonged (e.g. "de\\x1fnition").
     The byte->ligature map is *learned per file* from dictionary-validated letter-context
     evidence; every other control byte is a separator artifact and is stripped to a space.
  3. De-wrap                 — files hard-wrapped at a fixed column get paragraphs reflowed +
     de-hyphenated so multi-line phrases become greppable. Only runs on files detected as wrapped.
  4. ASCII ligature repair   — curated OCR non-words (fi->f: "defne"->"define") expanded when a
     single dictionary word results.

Operates on the (usually git-ignored) corpus. With `apply=True` it writes a timestamped backup
of every changed file BEFORE overwriting — backups go OUTSIDE the library tree by default (copies
of a copyrighted corpus must not accumulate where a .gitignore rule is the only guard), and only
the most-recent runs are kept.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from .common import glob_in
from .config import Config

LIG_MAP = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl",
           "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}  # U+FB05 is LONG-S+T = 'st'
CTRL = set(range(0x00, 0x09)) | {0x0b, 0x0c} | set(range(0x0e, 0x20))  # excl. \t \n \r
LIG_CANDS = ["ffi", "ffl", "fi", "fl", "ff", "ft", "st"]


def is_table_line(line: str) -> bool:
    """A GFM markdown table row (`| a | b |` or the `|---|---|` separator). docling emits tables
    this way; de-wrap and furniture-strip must leave them verbatim or the table is destroyed."""
    s = line.strip()
    return s.startswith("|") and s.endswith("|") and s.count("|") >= 2


def load_dict(path: str) -> set[str]:
    d: set[str] = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                w = line.strip().lower()
                if w:
                    d.add(w)
    return d


def fold_ligatures(text: str) -> tuple[str, int]:
    c = 0
    for k, v in LIG_MAP.items():
        n = text.count(k)
        if n:
            c += n
            text = text.replace(k, v)
    return text, c


def learn_ctrl(text: str, dic: set[str]):
    """Learn, per file, what each control byte means, from letter-context evidence."""
    occ = defaultdict(list)
    n = len(text)
    for i, ch in enumerate(text):
        if ord(ch) in CTRL and 0 < i < n - 1 and text[i - 1].isalpha() and text[i + 1].isalpha():
            j = i - 1
            while j >= 0 and text[j].isalpha():
                j -= 1
            k = i + 1
            while k < n and text[k].isalpha():
                k += 1
            occ[ch].append((text[j + 1:i], text[i + 1:k]))
    mapping, report = {}, {}
    for ch, toks in occ.items():
        best, bs = None, 0
        for cand in LIG_CANDS:
            s = sum(1 for (l, right) in toks if (l + cand + right).lower() in dic)
            if s > bs:
                bs, best = s, cand
        sep = sum(1 for (l, right) in toks
                  if len(l) > 1 and len(right) > 1 and l.lower() in dic and right.lower() in dic)
        if best and bs >= 2 and bs > sep:
            mapping[ch] = best
            report[f"0x{ord(ch):02X}"] = f"->'{best}' ({bs}/{len(toks)} words validated)"
        else:
            mapping[ch] = " "
            report[f"0x{ord(ch):02X}"] = f"->space (separator/symbol, {len(toks)} letter-ctx)"
    return mapping, report


def repair_ctrl(text: str, dic: set[str]):
    mapping, report = learn_ctrl(text, dic)
    n = len(text)
    out, c = [], 0
    for i, ch in enumerate(text):
        if ord(ch) in CTRL:
            c += 1
            if 0 < i < n - 1 and text[i - 1].isalpha() and text[i + 1].isalpha():
                out.append(mapping.get(ch, " "))
            else:
                out.append(" ")
        else:
            out.append(ch)
    return "".join(out), c, report


def is_wrapped(text: str) -> bool:
    # A wrapped file has NO paragraph-length line (max < 400) AND a low median line length.
    L = sorted(len(l) for l in text.split("\n") if l.strip())
    if not L:
        return False
    med = L[len(L) // 2]
    return L[-1] < 400 and med < 90


def dewrap(text: str, dic: set[str]) -> str:
    lines = text.split("\n")
    nb = sorted(len(l) for l in lines if l.strip())
    wrapw = nb[int(len(nb) * 0.9)] if nb else 80   # robust high percentile, not max
    short = wrapw * 0.72
    out, para = [], []

    def hyph_join(s, ln):
        ln = ln.strip()
        m = re.search(r"([A-Za-z]+)-$", s)
        if m:
            left = m.group(1).lower()
            rm = re.match(r"([A-Za-z]+)", ln)
            right = rm.group(1).lower() if rm else ""
            if left in dic and right in dic:
                return s + ln          # real compound: well- + known -> well-known
            return s[:-1] + ln          # soft wrap: behav- + ior -> behavior
        return (s + " " + ln) if s else ln

    def flush():
        if not para:
            return
        s = para[0].rstrip()
        for ln in para[1:]:
            s = hyph_join(s, ln)
        out.append(s)
        para.clear()

    for ln in lines:
        if is_table_line(ln):              # a markdown table row: flush prose, emit the row verbatim
            flush()
            out.append(ln)
        elif ln.strip() == "":
            flush()
            out.append("")
        else:
            para.append(ln)
            if len(ln.rstrip()) < short:   # short line = block/paragraph end
                flush()
    flush()
    res, b = [], 0
    for l in out:                          # collapse runs of blank lines
        if l == "":
            b += 1
            if b <= 1:
                res.append(l)
        else:
            b = 0
            res.append(l)
    return "\n".join(res)


# Curated, hand-verified ascii ligature-drop corrections. A generic dict rule is UNSAFE:
# common words missing from a word list get "repaired" into wrong words. Every key is a
# genuine OCR non-word with exactly one correct expansion. Extend per corpus as needed.
ASCII_CORRECTIONS = {
    "affne": "affine", "artifcial": "artificial", "defne": "define",
    "defned": "defined", "defnition": "definition", "fltration": "filtration",
    "fnal": "final", "fnancial": "financial", "fnely": "finely", "fner": "finer",
    "fnite": "finite", "frst": "first", "fssion": "fission", "fxed": "fixed",
    "modifcation": "modification", "nullifcation": "nullification",
    "refned": "refined", "refning": "refining", "scientifc": "scientific",
    "suffcient": "sufficient", "suffce": "suffice", "suffces": "suffices",
    "identifcation": "identification", "deflnitely": "definitely", "fllm": "film",
}
_ASCII_RE = re.compile(r"\b[A-Za-z]{3,20}\b")


def ascii_lig_repair(text: str):
    c, examples = 0, []

    def repl(m):
        nonlocal c
        w = m.group(0)
        fx = ASCII_CORRECTIONS.get(w.lower())
        if fx is None:
            return w
        if w.isupper():
            fx = fx.upper()
        elif w[0].isupper():
            fx = fx.capitalize()
        c += 1
        if len(examples) < 10:
            examples.append(f"{w}->{fx}")
        return fx

    return _ASCII_RE.sub(repl, text), c, examples


# ---- page furniture -------------------------------------------------------
# pdftotext/ebook dumps leave page-boundary junk between paragraphs — page numbers,
# roman folios, running heads, and OCR punctuation-noise ("/I", ",:", ",I' 'Ii"). It
# breaks grep two ways: a phrase wrapped across a page break gets a number/header spliced
# into it, and the junk lines pollute search. This removes WHOLE furniture lines only; a
# real content line is never touched. Conservative by design, and gated behind the same
# dry-run + backup as every other transform, because a wrong strip is silent data loss.
_ROMAN_RE = re.compile(r"(?i)^(?=[mdclxvi])m{0,4}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$")
_WORDRUN_RE = re.compile(r"[A-Za-z]{3,}")            # a plausible real word syllable
_SENTENCE_END = (".", "!", "?", ":", ";", ",", "—", "-", "”", "'")


def _is_page_number(s: str) -> bool:
    # ≤3 digits: catches page numbers but leaves 4-digit YEARS ("2003") alone.
    core = s.strip(" \t[](){}.·*|:—–-")
    return core.isdigit() and len(core) <= 3


def _is_roman_folio(s: str) -> bool:
    """A real folio is monotone-case and built from the small-number roman letters.
    This keeps name/word/abbreviation lookalikes ("Li", "Mi", "CM", "DI") — which are
    technically valid roman numerals — from being mistaken for page furniture."""
    if not 2 <= len(s) <= 7 or not _ROMAN_RE.match(s):
        return False
    return bool(re.fullmatch(r"[ivxl]{2,7}", s) or re.fullmatch(r"[IVX]{2,6}", s))


def _is_ocr_noise(s: str) -> bool:
    """A short line that is mostly punctuation and holds no real word ("/I", ",:",
    ",I' 'Ii"). Excludes anything with a digit (protects stats like ">7", "3.5") and
    the punctuation-majority test protects terse dialogue ("No.", "Yes!")."""
    if len(s) > 12 or _WORDRUN_RE.search(s) or any(c.isdigit() for c in s):
        return False
    core = [c for c in s if not c.isspace()]
    return bool(core) and sum(not c.isalnum() for c in core) / len(core) >= 0.5


def strip_page_furniture(text: str):
    """Drop standalone page furniture. Four conservative classes, whole-line only:
    page numbers, roman folios (len ≥2, so the pronoun 'I' is safe), OCR punctuation-
    noise, and running heads (a short, non-sentence line repeated ≥5× across the file)."""
    lines = text.split("\n")
    freq = Counter(l.strip() for l in lines if l.strip())

    def running_head(s: str) -> bool:
        # must contain a letter, so bare numbers/years never qualify as a "head"
        return (freq[s] >= 5 and len(s) <= 40 and len(s.split()) <= 6
                and any(c.isalpha() for c in s) and not s.endswith(_SENTENCE_END))

    out, removed, examples = [], 0, []
    for l in lines:
        if is_table_line(l):               # protect markdown table rows (incl. the |---| separator)
            out.append(l)
            continue
        s = l.strip()
        junk = bool(s) and (
            _is_page_number(s)
            or _is_roman_folio(s)
            or _is_ocr_noise(s)
            or running_head(s))
        if junk:
            removed += 1
            if len(examples) < 12:
                examples.append(s[:24])
        else:
            out.append(l)
    return "\n".join(out), removed, examples


def process(text: str, dic: set[str]):
    stats = {}
    text, stats["ligature"] = fold_ligatures(text)
    text, stats["ctrl"], stats["ctrl_map"] = repair_ctrl(text, dic)
    # strip furniture BEFORE de-wrap, or a page number reflows into a paragraph
    text, stats["furniture"], stats["furniture_ex"] = strip_page_furniture(text)
    wrapped = is_wrapped(text)
    stats["dewrapped"] = wrapped
    if wrapped:
        text = dewrap(text, dic)
    text, stats["ascii_lig"], stats["ascii_ex"] = ascii_lig_repair(text)
    return text, stats


def prune_backups(root: str, keep: int) -> list[str]:
    """Keep only the `keep` most-recent backup run dirs under root; delete older."""
    if not os.path.isdir(root):
        return []
    runs = [os.path.join(root, d) for d in os.listdir(root)
            if d.startswith("normalize-backup-") and os.path.isdir(os.path.join(root, d))]
    runs.sort(key=os.path.getmtime, reverse=True)
    pruned = []
    for old in runs[max(1, keep):]:       # never prune below 1 — protects the run just written
        shutil.rmtree(old, ignore_errors=True)
        pruned.append(old)
    return pruned


@dataclass
class NormResult:
    changed: int = 0
    dry_run: bool = True
    backup_dir: str | None = None
    unreadable: list = field(default_factory=list)   # shelves glob could not list — a scan that
    #                                                  could not run, never silently zero matches
    pruned: int = 0
    details: list[tuple[str, list[str]]] = field(default_factory=list)   # (relpath, flags)
    skipped: list[str] = field(default_factory=list)
    refused: str | None = None
    scanned: int = 0
    """How many files were actually read. `changed == 0` is printed identically whether every file
    was clean or NO file was read at all, so the gate could pass having inspected nothing."""


def normalize(cfg: Config, *, pattern: str = "*/*.txt", apply: bool = False,
              stamp: str | None = None) -> NormResult:
    """Normalize corpus files matching `pattern` under the library dir. Confined to real .txt
    files inside the library tree — a crafted pattern can never read/overwrite outside it."""
    dic = load_dict(cfg.dict_path)
    res = NormResult(dry_run=not apply)
    if not dic:
        if apply:
            # ctrl/ascii repair without a word list strips control bytes blindly — destructive.
            res.refused = (f"REFUSING apply without a word list at {cfg.dict_path} "
                           "(ctrl/ascii repair would strip bytes destructively)")
            return res
        res.skipped.append(f"no word list at {cfg.dict_path} — ctrl/ascii repair degraded (dry-run)")

    lib_real = os.path.realpath(cfg.lib)
    # `glob` turns a directory it cannot read into ZERO matches, with no error anywhere. A shelf
    # that goes unreadable mid-scan therefore left `scanned` truthy and `skipped` empty, and
    # `normalize --check` certified a corpus it had only partly read — verified: 1 of 2 files
    # scanned, exit 0, nothing reported. Listability is checked up front so the gap is stated.
    for shelf in cfg.shelves:
        d = os.path.join(cfg.lib, shelf)
        if not os.path.isdir(d):
            continue
        try:
            os.scandir(d).close()
        except OSError as e:
            res.unreadable.append(f"{shelf}: {e}")
    # An ABSOLUTE pattern makes `glob` ignore `root_dir` entirely, which puts the library prefix
    # back into the pattern string and re-arms every metacharacter in it. The pattern is relative
    # to the library by definition, so an absolute one is a mistake worth naming.
    if os.path.isabs(pattern):
        res.refused = (f"pattern {pattern!r} is absolute; it must be relative to the library dir "
                       f"({cfg.lib}) — an absolute pattern silently bypasses path handling")
        return res
    files = []
    # `pattern` is the USER's and stays live (it may carry `/`); `glob_in` keeps the
    # library directory out of the pattern string entirely, so it needs no escaping
    for p in sorted(glob_in(cfg.lib, pattern)):
        rp = os.path.realpath(p)
        if rp.startswith(lib_real + os.sep) and rp.endswith(".txt") and os.path.isfile(rp):
            files.append(p)
        else:
            res.skipped.append(f"outside library or not a .txt: {p}")

    backup_root = str(cfg.backup_dir) if cfg.backup_dir else os.path.join(
        tempfile.gettempdir(), "klode-normalize-backups")
    # Each run gets its OWN dir, so a later run never overwrites an earlier run's pristine backup
    # and `prune_backups` (keep-newest-N) has distinct runs to prune. Was a constant `run` — a
    # second apply clobbered the original backup, making the change irreversible.
    run_stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    # `--stamp` is user input and lands in a path. `../../../tmp/x` normalised straight out of the
    # backup root, so the copies of a copyrighted corpus this function exists to protect could be
    # written anywhere the process can write. One safe component only.
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", run_stamp) or run_stamp in (".", ".."):
        raise ValueError(f"invalid stamp {run_stamp!r}: use letters, digits, '.', '_', '-' "
                         "(one path component, max 64 chars)")
    backup_dir = os.path.join(backup_root, f"normalize-backup-{run_stamp}")
    if not os.path.normpath(backup_dir).startswith(os.path.normpath(backup_root) + os.sep):
        raise ValueError(f"refusing to write backups outside {backup_root}")

    res.scanned = len(files)
    for path in files:
        # Decode STRICTLY: errors='replace' would persist U+FFFD on apply — irreversible loss.
        try:
            with open(path, encoding="utf-8") as f:
                orig = f.read()
        except UnicodeDecodeError as e:
            res.skipped.append(f"not valid UTF-8 ({e}): {os.path.relpath(path, cfg.lib)}")
            continue
        new, st = process(orig, dic)
        if new == orig:
            continue
        res.changed += 1
        rel = os.path.relpath(path, cfg.lib)
        flags = []
        if st["ligature"]:
            flags.append(f"ligature={st['ligature']}")
        if st["ctrl"]:
            flags.append(f"ctrl={st['ctrl']} {st['ctrl_map']}")
        if st["furniture"]:
            flags.append(f"furniture-stripped={st['furniture']} {st['furniture_ex']}")
        if st["dewrapped"]:
            flags.append("DEWRAP")
        if st["ascii_lig"]:
            flags.append(f"ascii-lig={st['ascii_lig']} {st['ascii_ex']}")
        res.details.append((rel, flags))
        if apply:
            dst = os.path.join(backup_dir, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(path, dst)
            with open(path, "w", encoding="utf-8") as f:
                f.write(new)

    if apply and res.changed:
        res.backup_dir = backup_dir
        res.pruned = len(prune_backups(backup_root, cfg.backup_keep))
    return res
