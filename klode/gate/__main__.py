"""`python3 -m klode.gate` — the CriterionSpec authoring tool.

    derive  -c LIB <dimension>    seed a candidate rubric from the Craft layer (never overwrites)
    check   -c LIB <dimension>    validate a rubric fail-closed; the errors are the author's worklist
    approve -c LIB <dimension>    mark human_approved, binding the approval to this exact body
    repin   -c LIB <dimension>    re-pin the corpus fingerprint after a re-review (resets approval)

Deliberately NOT a `klode` CLI subcommand: the gate is the experimental layer, and the lib's
operation registry is the anti-drift contract for the stable CLI/MCP surface. Authoring a rubric is
a maintainer's task, not part of the consumption API.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from klode import lib

from . import authoring, spec as _spec


def _cfg(args):
    return lib.Config.load(Path(args.config))


def _target(cfg, dimension: str) -> Path:
    """The rubric path for a dimension, refused if it escapes the criteria directory.

    `dimension` comes from argv, so an absolute or `../` value would make `derive`/`repin` WRITE
    outside the KB. `_spec.spec_path` owns the grammar and containment check; it returns None only
    when the file does not exist yet, which is exactly the derive case."""
    if not cfg.criteria:
        raise SystemExit("this KB has no [frameworks].criteria dir — enable [frameworks] and set "
                         "`criteria = \"_criteria\"` in library.toml")
    try:
        _spec.spec_path(cfg, dimension)          # validates the grammar + containment
    except _spec.SpecError as e:
        raise SystemExit(str(e))
    base = Path(cfg.criteria).resolve()
    return base / f"{dimension}.json"


def _write(path: Path, doc: dict, *, exclusive: bool = False) -> None:
    """Atomic write. A direct `write_text` truncates first, so an interruption mid-write leaves the
    only approved rubric empty — the artifact whose whole job is to be trustworthy."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    # mkstemp: an UNPREDICTABLE name created O_EXCL at mode 0600, in the target's own directory.
    # A name derived from the target and PID is guessable, and opening it with plain "w" follows a
    # symlink an attacker planted there — truncating whatever it points at.
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        if exclusive:
            # `os.link` creates the destination atomically WITH its full content, and fails if it
            # already exists. Writing into an O_EXCL fd is exclusive but not atomic: an interrupted
            # run, or a concurrent reader, can still see a half-written candidate.
            try:
                os.link(tmp, path)
            except FileExistsError:
                raise SystemExit(f"refusing to overwrite {path} — edit it, or delete it first")
            except OSError:
                # no hard-link support (some network/FAT filesystems). Fall back to the exclusive
                # create the link was an improvement on — still never overwrites, but a crash
                # mid-write can leave a partial candidate. Say so rather than fail the command.
                try:
                    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644),
                                   "w", encoding="utf-8") as fh:
                        fh.write(body)
                except FileExistsError:
                    raise SystemExit(f"refusing to overwrite {path} — edit it, or delete it first")
                print("note: this filesystem has no hard links; the candidate was written "
                      "non-atomically", file=sys.stderr)
        else:
            os.replace(tmp, path)          # consumes tmp
            tmp = None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def cmd_derive(args) -> int:
    cfg = _cfg(args)
    path = _target(cfg, args.dimension)
    try:
        doc = authoring.derive(cfg, args.dimension, levels=args.levels)
    except ValueError as e:
        print(f"cannot derive: {e}", file=sys.stderr)
        return 1
    _write(path, doc, exclusive=True)
    print(f"wrote candidate rubric {path}\n\n"
          "It does NOT validate yet, by design. Run `check` — each error names a field a human must\n"
          "fill: the warrant for every inferred claim, and a descriptor for every level. Then set\n"
          '`"admission": "human_approved"`.')
    return 0


def cmd_check(args) -> int:
    cfg = _cfg(args)
    try:
        spec = _spec.load(cfg, args.dimension, require_stamp=not args.no_stamp)
    except _spec.SpecError as e:
        print(f"INVALID: {e}", file=sys.stderr)
        return 1
    state = "human_approved — the gate will score against this" if spec.approved else \
            "candidate — VALID but the gate will refuse it until a human approves it"
    print(f"OK: {spec.dimension} — {len(spec.criteria)} criteria over panel {list(spec.panel)}\n"
          f"admission: {state}")
    for c in spec.criteria:
        kinds = ", ".join(sorted({f.kind for f in _spec._all_fields(c)}))
        print(f"  {c.id}  levels 0..{c.max_score}  [{kinds}]")
    return 0


def _load_doc(path: Path) -> dict:
    """Read + structurally validate a rubric before mutating it. Rewriting raw JSON blind meant a
    non-object file raised AttributeError, and a structurally invalid rubric could be re-pinned and
    reported as done."""
    if not path.is_file():
        raise SystemExit(f"no rubric at {path}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_spec._no_duplicate_keys)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise SystemExit(f"{path}: cannot be read as JSON — {e}")
    except _spec.SpecError as e:
        raise SystemExit(f"{path}: {e}")
    try:
        _spec.parse(doc)                      # structure only; the corpus may legitimately have moved
    except _spec.SpecError as e:
        # an already-approved rubric whose body was edited fails here, which is the point
        raise SystemExit(f"{path}: not a valid rubric, refusing to rewrite it — {e}")
    return doc


def cmd_repin(args) -> int:
    cfg = _cfg(args)
    path = _target(cfg, args.dimension)
    doc = _load_doc(path)
    doc["fingerprint"] = authoring.fingerprint(cfg, doc.get("panel") or [])
    # Re-pinning asserts a human re-read the sources against the rubric. Silently keeping the
    # approval would let a source change slip into canon under an old human's signature.
    if doc.get("admission") == "human_approved":
        doc["admission"] = "candidate"
        doc.pop("approved_digest", None)
        print("admission reset to 'candidate' — the corpus moved; re-approve after re-reading")
    _write(path, doc)
    print(f"re-pinned {path}")
    return 0


def cmd_approve(args) -> int:
    """Mark a rubric human_approved and bind the approval to this exact body.

    The digest does not prove a human was involved — nothing in a file can. It proves the approved
    BYTES have not changed since approval, so approve-then-edit is detected instead of inherited."""
    cfg = _cfg(args)
    path = _target(cfg, args.dimension)
    doc = _load_doc(path)
    doc["admission"] = "candidate"                        # digest covers the body, not the verdict
    doc.pop("approved_digest", None)
    try:
        _spec.validate(cfg, _spec.parse(doc), require_stamp=not args.no_stamp)
    except _spec.SpecError as e:
        print(f"NOT approved — the rubric does not validate: {e}", file=sys.stderr)
        return 1
    doc["admission"] = "human_approved"
    doc["approved_digest"] = _spec.content_digest(doc)
    _write(path, doc)
    print(f"approved {path}\ndigest {doc['approved_digest'][:16]}… — any later edit invalidates it")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python3 -m klode.gate", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-c", "--config", required=True, help="path to library.toml")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("derive", help="seed a candidate rubric from the Craft layer")
    d.add_argument("dimension")
    d.add_argument("--levels", type=int, default=6, help="number of behavioral levels (default 6 => 0..5)")
    d.set_defaults(fn=cmd_derive)
    c = sub.add_parser("check", help="validate a rubric fail-closed")
    c.add_argument("dimension")
    c.add_argument("--no-stamp", action="store_true", help="allow unstamped sources to ground")
    c.set_defaults(fn=cmd_check)
    r = sub.add_parser("repin", help="re-pin the corpus fingerprint (resets approval)")
    r.add_argument("dimension")
    r.set_defaults(fn=cmd_repin)
    ap = sub.add_parser("approve", help="mark human_approved, binding the approval to this body")
    ap.add_argument("dimension")
    ap.add_argument("--no-stamp", action="store_true")
    ap.set_defaults(fn=cmd_approve)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
