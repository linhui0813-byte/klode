# A consumer cannot pin what it cites: the library has no fingerprint, so downstream claims rot silently

**Status:** dev-note / feature request, from the consumer seat. Written 2026-07-28 in the same
seat as `consult-resolution-and-mcp-parity-defects-2026-07-28.md` (all seven of those are fixed).
This one is not a defect in what lodlib does — it is a gap in what lodlib **lets a downstream repo
do**.

---

## The incident

A downstream repo (`~/stories/a-school-yard`) keeps reviewed, tiered knowledge pages. One of them
is a survey of *this* library: what shelves exist, how many cards, how many frameworks, which
syntheses. It had been human-reviewed and promoted to the repo's highest tier (`canonical`).

Eight days later, during an unrelated task, it turned out every number on it was wrong:

| the page claimed | actual |
|---|---|
| 109 grep-anchored cards | **179** |
| 19 framework distillations | **65** |
| 9 dimension syntheses | **22** |
| 5 shelves | **6** — a whole new `worldbuilding` shelf, 51 sources |

The page's *conclusion* still held. Only its facts about the library had rotted.

**It was caught by accident.** Nothing flagged it, and nothing could have.

---

## Why nothing could have

The consumer repo has a staleness ledger: it records `{artifact_path, sha256}` for the artifacts a
page's claims rest on, and re-hashes them at review time. Its own protocol requires the path to
**stay inside the repo** before it is opened — absolute paths, `..`, and symlink escapes are
rejected. That is a correct security boundary and should not change.

But this library lives outside that repo (mounted read-only through a symlink). So:

> **The one artifact the page is entirely about is the one artifact the ledger is forbidden to
> record.** The ledger for that page is empty — not by oversight, by construction.

## What lodlib already guards, and what it doesn't

lodlib's integrity story is genuinely strong — in one direction:

| guard | direction | where |
|---|---|---|
| citation-rot linter | **quote → source**: does this quoted phrase still occur? | `lib check` |
| `source_sha256` stamping | **source → card**: did the source text change under a stamped card? | `check.py`, `lib build --stamp` |
| — | ## **library → downstream claim about the library** | ## **nothing** |

Both existing guards run *inside* the library and protect the library's internal consistency. A
consumer that makes claims *about the corpus as a whole* — its size, its shelves, its dimension
list, its tiers — has nothing to record and nothing to re-check.

`lib check` today prints:

```console
$ lib check
library check — 179 cards, 179 shelf sources

OK: 0 errors, 0 warning(s)
```

Human-readable, and the counts are right there. But there is nothing a downstream repo can **store
in one line and compare later** without walking the library tree itself.

## The tier leak is the sharper half

Counts are the visible symptom. The one that actually corrupts downstream reasoning is **tier**.

The consumer repo cites the `worldbuilding` dimension and is careful to say, every time, that its
tier is `proposed` — not settled doctrine. That care is worthless the moment it drifts: if
`worldbuilding` is promoted to `canonical` (or demoted, or contested) upstream, **the consumer keeps
repeating the old tier with full confidence, and its own review gate has no way to catch it.**

> lodlib is explicit that the tier travels with the claim. Right now the tier travels only as far
> as the process boundary.

---

## Request: a pinnable fingerprint

Something a consumer can store as one opaque string and re-compare later, without filesystem access
to the library:

```console
$ lib check --json
{
  "cards": 179,
  "shelves": {"craft": 34, "narratology": 6, "papers": 57, "science": 41,
              "secondary": 18, "worldbuilding": 51},
  "frameworks": 65,
  "syntheses": 22,
  "tiers": {"canonical": 16, "proposed": 6},
  "fingerprint": "sha256:…"
}
```

Where `fingerprint` is a digest over a **sorted, order-independent** list of
`(card_id, source_sha256, tier)` — plus dimension stems and their `status:`. Properties that matter
to a consumer:

- **stable** — same library, same string, regardless of walk order or filesystem;
- **cheap** — one field to store next to a page, one command to re-derive;
- **discriminating on tier** — a promotion or demotion upstream changes it, which is the whole point;
- **not a version number** — the library has no releases; the corpus is the version.

A `lib fingerprint` alias printing just that one line would make the downstream integration a
one-liner in a review script.

### Where it belongs (given the P2/P3 facade that just landed)

`lodlib/__init__.py` now exports a public surface — `consult`, `resolve`, `verify`, `dimension`,
`framework`, `diagnose`, `search`, `__version__`. **`fingerprint` belongs in exactly that list**,
next to `diagnose`, for the same reason `diagnose` was moved there last round: one computation, and
the CLI, the MCP server, and a downstream script all render the same object.

⚠ **And it is emphatically not `__version__`.** That is the *tool's* version and it says nothing
about the corpus — the two drift independently, and a consumer pinning `__version__` would be
pinning the wrong thing while believing it had solved the problem. If both end up in the JSON, they
need names that cannot be confused: `tool_version` and `corpus_fingerprint`.

### Nice-to-have, only if it is cheap

`--json` on `list_lenses` (and the MCP tool) carrying each dimension's `status:` machine-readably.
The text form already prints the tier; a consumer parsing prose to learn a tier is exactly the kind
of thing that silently breaks.

---

## What I am **not** asking for

- **Not** a relaxation of the consumer's in-repo path check. That is a security boundary in the
  other project, it is correct, and this request exists precisely so it does not have to move.
- **Not** for lodlib to write anything into consumer repos, or to know they exist. The fingerprint
  is pull-only: the consumer stores a string it got from a command it ran.
- **Not** a new subsystem. Everything above is derivable from data `lib check` already walks. Same
  shape as the `diagnose` fix in the previous round: compute it once in a shared place, let the CLI
  and MCP render it.

---

## Why this is worth doing at all

The pitch for this library is that a claim citing it is *checkable* rather than second-hand. That
promise currently stops at the process boundary: inside the library, a rotted quote is caught
mechanically; one directory up, a rotted claim about the library survives a human review gate and
gets stamped as fact.

> **A number that was true when it was reviewed, and is false now, with a review stamp on it, is
> worse than one that was never checked.** The stamp is what makes it dangerous.

One digest closes it.
