# `consult` resolution + CLI/MCP parity — seven defects found from the consumer seat

> **✅ Resolved 2026-07-28** — all seven fixed in `5ea3c46` (branch `fix/consult-resolution-parity`,
> merged to `main`): §1 dimension-family resolution · §2 config-pointed schema (no hardcoded names) ·
> §3 `query.diagnose` + a new MCP `diagnose` tool · §4 per-cue CJK matcher · §5 Craft-default hint ·
> §6 `query.resolve` / `Resolution` (one status→response policy, both frontends format it) ·
> §7 `dialogue.md` core-question field. **114 tests pass, `lib check` 0 / 0.**

**Status:** dev-note / bug report. Written 2026-07-28 while using the `craft-library` MCP and the
`lib` CLI as a *consumer* — mapping a ten-layer novel-writing pipeline onto the library, one layer
at a time, against the doxai corpus (179 cards, 6 shelves, 65 frameworks, 22 syntheses). Same seat
as `synthesis-audience-projection-2026-07-24.md`; that note was about *what* a synthesis returns,
this one is about *whether you reach the right one at all*.

Everything below was reproduced against `library.toml` in
`/Users/joker/github/xiaolai/myprojects/doxai`. No source file was modified.

Relevant history: `21eb6ee` — *"audit-fix: harden consult/diagnose parsing, kill CLI/MCP drift"*.
The drift came back, in a different place. §3 and §6 argue that is structural, not incidental.

---

## Severity summary

| # | Defect | Class |
|---|--------|-------|
| **1** | `viewpoint` resolves — silently, uniquely — to `stockwell` (a prose/style card) | **wrong answer** |
| **2** | MCP tool schemas advertise dimension names that no longer exist | **wrong answer** |
| 3 | `diagnose` is CLI-only; the logic lives in `cli.py`, so MCP structurally cannot reach it | capability gap |
| 4 | Diagnostic cue matching is dead for any non-ASCII cue, and fails silently | silent no-op |
| 5 | `diagnose` prints `--section spec`, which (a) does not exist on newer dimensions and (b) points at the engine layer | wrong hint |
| 6 | Same input, different behavior in CLI vs MCP | drift |
| 7 | `dialogue` has no core question; `list_lenses` emits a bare line | content gap |

---

## 1. `viewpoint` silently resolves to Stockwell — **wrong answer, no warning**

```console
$ lib consult viewpoint
# Framework Card — Peter Stockwell, *Texture: A Cognitive Aesthetics of Reading* (2009)
dimension: Prose / style
```

```
consult_dimension(dimension="viewpoint")
→ "`viewpoint` resolves to a framework (`stockwell`), not a craft dimension —
   call consult_framework(name=\"stockwell\")."
```

Stockwell is a **prose/style** card. The library has **two** viewpoint dimensions
(`viewpoint-who`, `viewpoint-showing`) and **four** viewpoint frameworks (`booth`, `gardner`,
`genette`, `wood`). None of them is offered.

**Root cause** — `query.py:resolve_consult` (298):

1. `viewpoint` passes `_SAFE_STEM`, so the exact-stem path runs first: `dimension(cfg, "viewpoint")`
   (query.py:304) misses, because the real stems are `viewpoint-who` / `viewpoint-showing`. No
   prefix or family fallback exists.
2. Resolution falls through to token scoring. The haystack (query.py:333) is
   `stem + title + aliases + source title/aliases` — it **does not include the `**Dimension:**`
   line**.
3. Of all framework cards, only `stockwell.md:4` happens to carry `cognitive viewpoint` in its
   alias list. The four real viewpoint cards say `point of view` / `focalization` instead.
4. So `full` has exactly one member → status `unique` → returned with confidence.

> One incidental adjective inside one card's alias list outranks an entire dimension family.

**Suggested fix** — in order of value:

- **(a)** Before token scoring, try dimension stems by **prefix / family**: `viewpoint` →
  `viewpoint-who`, `viewpoint-showing` → return `ambiguous` with those two. This alone fixes the
  observed case and is the general guard against the next incidental alias.
- **(b)** Consider folding the `**Dimension:**` line into the haystack, or weighting a dimension-name
  token above an alias token, so a query that *names a dimension family* can never be won by a
  framework.
- **(c)** When status is `unique` but the query string is also a **substring of ≥2 dimension stems**,
  downgrade to `ambiguous` rather than answering.

---

## 2. The MCP schemas advertise dimension names that do not exist

Hardcoded in `mcp_server.py`:

- `list_lenses` description (48–58): `attention, curiosity, emotion, gap-surprise, presence,
  suspense, value-decision, viewpoint`
- `consult_dimension.dimension` description (74): `e.g. suspense, emotion, attention, presence,
  viewpoint, curiosity, gap-surprise, value-decision`

Against the current corpus:

| advertised | actual | behavior when called |
|---|---|---|
| `emotion` | `emotion-nature` | disambiguation prompt (recoverable) |
| `presence` | `reader-immersion` | disambiguation prompt (recoverable) |
| `viewpoint` | `viewpoint-who` / `viewpoint-showing` | **wrong answer, see §1** |

Three of the eight advertised names are dead. This matters more over MCP than over the CLI:
**an agent sees the schema before it sees any output.** A schema that names non-existent values is
a hallucination source that the library itself is supplying.

**Suggested fix:** build the example list at server start from `query.lenses(cfg)` — or drop
concrete names from the descriptions entirely and say "call `list_lenses` first." Either way the
strings must stop being hand-maintained; they have already drifted once.

---

## 3. `diagnose` is unreachable from MCP — and the reason is architectural

The symptom router exists only as `cli.py:cmd_diagnose` (388). Its three parts — `_load_diagnostics`
(390), the cue scorer (398), the renderer (410–417) — are all in the CLI module. `mcp_server.py`
imports only `query`, so it cannot call any of it.

Consequence for a consumer: the five agents wired to this MCP have **no symptom → dimension
routing**. They must self-route from `list_lenses`, which is exactly the step `diagnose` exists to
remove.

**Suggested fix — not "add a seventh tool", but move the logic down:**

```python
# query.py
def diagnose(cfg, symptom: str) -> list[tuple[str, str]]:   # [(dimension, core_question)]
    ...
```

CLI and MCP then each render it. This is the same fix that closes §6.

---

## 4. Non-ASCII cues never fire, and nothing says so

`cli.py:398`:

```python
return sum(bool(re.search(rf"\b{re.escape(c)}\b", symptom)) for c in cues)
```

Reproduced:

| cue | symptom | hits |
|---|---|---|
| `info dump` | `there is an info dump here` | 1 |
| `信息倾倒` | `这里信息倾倒了` | **0** |
| `信息倾倒` | `信息倾倒` (alone) | 1 |
| `信息倾倒` | `the 信息倾倒 problem` | 1 |

CJK characters are `\w` under Python's Unicode-default `str` patterns, so there is no `\b` between
two of them. A CJK cue therefore matches only when it happens to sit against an ASCII/space
boundary — i.e. never, inside a real sentence in that language.

This is sharper than it looks, because `_diagnostics.md` explicitly invites it:

> *"Hand-edit freely — add cues in your own vocabulary."*

Add cues in a CJK vocabulary and they are inert, with no error and no warning.

**Suggested fix:** choose the matcher per cue, not globally.

```python
def _cue_hit(cue: str, symptom: str) -> bool:
    if re.search(r"[A-Za-z0-9]", cue):
        return bool(re.search(rf"\b{re.escape(cue)}\b", symptom))
    return cue in symptom          # \b is meaningless for a cue with no ASCII word chars
```

(`(?<!\w)…(?!\w)` does **not** help — same root cause.)

---

## 5. The `--section spec` hint is wrong twice over

`cli.py:417` prints, for every routed dimension:

```
→ lib consult <dim> --section spec
```

**(a) It does not exist on newer syntheses.**

```console
$ lib consult worldbuilding --section spec
(no section matching 'spec' — sections: craft, the answers, side by side, live tensions, owed)
```

**(b) Where it does exist, it is the engine layer** — `## Operational spec for the engine`. Per this
repo's own audience projection (`synthesis-audience-projection-2026-07-24.md`), that is precisely
the layer a writer should *not* be handed. `diagnose` is the writer-facing entry point, and it
currently points every writer at the scorer mapping.

**Suggested fix:** default the hint to the craft layer — `--section craft`, or no `--section` at all
(the writer projection is already the default for `lib consult`).

---

## 6. Same input, different behavior across the two frontends

| input | CLI | MCP |
|---|---|---|
| `viewpoint` | prints the Stockwell card outright | states it is a framework, not a dimension |
| `presence` | disambiguation list | disambiguation list |

MCP's handling is the better of the two, and neither is correct (§1). The divergence is not in
`resolve_consult` — that is shared — but in **what each frontend does with the returned status**.
That policy is duplicated in two renderers, which is why `21eb6ee` did not hold.

**Suggested fix:** move status→response policy into `query` (e.g. return a small
`Resolution` object carrying the intended user-facing outcome), leaving CLI and MCP with formatting
only. Pairs with §3.

---

## 7. Minor: `dialogue` has no core question

`list_lenses` emits the dimension name with an empty second line. Content gap in
`frameworks/_syntheses/dialogue.md`, not a code defect — noted because the output looks broken.

---

## Suggested order

| | change | why here |
|---|---|---|
| 1 | Generate the schema's example names from the config (§2) | one edit; stops the library from advertising dead values |
| 2 | Dimension-family prefix beats alias scoring (§1) | root-cause fix; also guards the next incidental alias |
| 3 | Move `diagnose` + resolution policy into `query` (§3, §6) | the structural fix; drift keeps returning without it |
| 4 | Per-cue matcher selection (§4) | one function; non-ASCII routing is dead until then |
| 5 | Hint `--section craft` (§5) | one line |

---

## Not a lodlib defect (recorded so it is not filed as one)

A consumer repo mounting the library through a symlink (`…/research/craft/_lodlib-library →
doxai/library`) cannot find it with a recursive grep from the repo root: neither `grep -r` nor
ripgrep follows symlinks by default, and both exit 0 with no output. That is standard tool
behavior, and it belongs in the consumer's own docs — not here.
