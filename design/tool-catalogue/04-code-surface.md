# 04 — Code surface

The authoritative per-file change list. Realizes `00-overview.md`; depends on
`01`, `02`, `03`.

Phase 1 is §1–§6. Phase 3 is §8. Verify each against the section that decided
it — this file says *where*, not *why*.

## 1. The catalogue generator — `nora/agent/registry.py`

`ToolRegistry.capability_digest()` (line 569) already renders `name — first
sentence`, one line per tool, derived from the registry. Reuse it; do not
write a second renderer.

- Generalise the read-only filter into a parameter. The Relevance Gate keeps
  `read_only_tools()`; the tag catalogue takes all 54. **Its docstring's
  "READ-ONLY tools only" paragraph is the contract the gate depends on** — it
  must keep describing the gate's call, not the new one.
- Add the hot-set marker (`*`) and the block header from `01`.
- The hot set is a derived value, not a literal: it comes from the measured
  call counts in `01`, and `05` §4 sets when it is re-derived. Wherever it
  lands, it is **one** declaration read by both the renderer and the builder —
  a marker in the catalogue that disagrees with what `build()` loaded is worse
  than no marker.

**Do not delete `capability_digest`'s existing call path.** The gate's use of
it is load-bearing (#144) and a shared renderer with one caller changed is the
cheap version of this change; a fork is the expensive one.

## 2. Line discipline — `nora/agent/tools.py`

The catalogue line is each tool's **first sentence**, so the first sentence
becomes a gated surface.

- **Repair `where_is`.** Measured under-discrimination against `where_been`:
  "where somebody in this conversation was last seen" does not carry RIGHT
  NOW, which is the whole boundary. The full description already states it
  ("forcing a fresh reading first so the answer is where they ARE"); the
  first sentence must.
- **Cap the two over-budget lines:** `recall_lookups` (46 tokens),
  `set_music_volume` (45). Target ≤20.
- **New test:** every tool's rendered catalogue line is ≤ 20 tokens, and the
  whole block ≤ 1,400. This is the gate that stops the catalogue rotting back
  into a second copy of the descriptions — which is what DEC-150 deleted.
- **New test:** no two tools render an identical line.

`nora/agent/tools.py` is a shared file — whitefish-c0 held it this session for
`social_fetch`. Check `git status` before editing.

## 3. The `load_tools` verb — `nora/agent/tools.py`, `NATIVE_TOOLS`

One `NativeTool` entry with every field stated (`02` has the table and the
reasoning): `budget=3`, `terminal=False`, `time_shiftable=None`,
`read_only=True`, `source_tier=None`, `plugin=False`.

`_spec()` raises `KeyError` on a name absent from `NATIVE_TOOLS` — R-7's
default-deny arriving at build time. That covers registration; nothing extra
is needed to refuse an invented name at call time.

The closure resolves names against the registry and returns the R-27 shape
from `02`. A partial resolve is `ok` with non-empty `unknown`, never
`nothing_found`.

## 4. Deferred loading — `nora/agent/registry.py` `specs()` / `build()`

`build()` returns `list[Any]` for `Agent(tools=...)`. Today it adapts every
spec. It must now return the hot set + `load_tools` + any MCP groups, and hold
the rest for `load_tools` to hand back.

**Return schemas in the `tool_result` body, not by mutating the `tools`
array** (`02` §"Interaction with prompt caching"). The cached prefix is 30,080
tokens today and is the reason this is a first-call problem rather than a
per-call one.

**This is gated on §2b of `05` passing.** Whether DeepSeek via OmniRoute will
emit a `tool_call` for a tool absent from the `tools` array is unverified. A
negative result forces array mutation, which costs the cache and needs its own
measurement before anything else is built.

## 5. Turn ceilings — `nora/config.py`

`load_tools` spends from the shared ceiling like any verb. `turns` defaults to
6 and `tool_calls` is a per-name dict (`nora/config.py:2630`).

Expected `load_tools` calls per turn: 0.8 mean (72.8% of turns need ≥1 cold
tool, most needing one call). Raise the shared ceiling by that much or the
change silently costs a real tool call — the failure recorded in
`per-tool-budget-is-inert-under-a-shared-pass-ceiling`.

**Do not state the cap in the catalogue text.** Measured (`nora/config.py`
~2578): stating a hard cap in words cost one case 5/5 → 1/5, because a model
told it has a hard cap declines the work rather than spending less.

## 6. Doctrine tier — `nora/doctrine/loader.py`

The catalogue is a **derived** kind at order 35, between `conduct` and
`constraints` — the slot DEC-150 vacated.

- `DERIVED_KINDS` (line 77) is `("situation",)`; add the catalogue's kind.
- `AUTHORITY_TERMINALS` (line 103) and the `authority_above`/`authority_below`
  chains in `identity.md`, `conduct.md`, `constraints.md`,
  `participation-conduct.md`, `silence.md`, `voice.md` all name the tiers
  relatively. Every chain that spans the new rung needs the rung inserted, and
  `check_authority_graph` refuses a pair whose two ends disagree. **A doctrine
  file edit is a `version:` bump** — `tests/test_doctrine_version_parity.py`.
- `check_kind_boundaries` (line 397) is scoped to `identity`/`conduct`; a
  derived kind is untouched by the "may not name a tool" gate. `tool_names()`
  (line 555) stays as it is.
- The loader refuses a *file* claiming a derived kind (line 605) — that guard
  is what stops someone pasting a rendered catalogue into `nora/doctrine/`,
  and it must keep working for the new kind.

Comments at loader lines 10, 61 and 503 narrate `capability` as gone. They
become wrong the moment this lands; update them rather than leaving prose that
reads as current. (`a-late-reversal-is-not-landed-until-every-quoting-file-changes`.)

## 7. Observability — `nora/obs/`

- `nora_tool_calls_total{tool="load_tools"}` — free, the existing counter.
- **A counter for the DEC-28 failure**: turns that ended without a reply while
  the room named a capability the catalogue carries. This is the one metric
  that distinguishes "she chose not to" from "she never saw it", and DEC-28's
  lesson is that without it a grep returns zero either way.
- Catalogue token count as a gauge at build time, so the ≤1,400 ceiling is
  observable in production and not only in a test.

Metrics are append-only (R-17): nothing is removed, and a tool that stops
being called leaves a frozen series.

## 8. Phase 3 — result handling (`03`)

Not phase 1. Per tool: cut the result-handling paragraphs from the docstring,
leave the one-line pointer, attach the prose to the returned payload. The
`output_contract` field on `ToolSpec` (ADR 0009 / DEC-153) is already read at
runtime in `adapt()` (`nora/agent/ports.py`) and is the seam.

**Constraint from prior review:** `ToolSpec` stays a frozen dataclass and
`strands` imports stay inside `nora.agent.**`. The validation seam is wrapping
`spec.call` inside `adapt()`; do not add a second seam in `specs()`/`build()`.

## Tests that will break

`tests/test_doctrine_content.py` (32 tests, several pinning tier order and
authority chains), `tests/test_doctrine_assembly.py`,
`tests/test_doctrine_loader.py`, `tests/unit/test_tool_registry.py`
(`test_budgets_match_todays_table` pins the budget dict by literal),
`tests/test_prefill.py`, `tests/test_composition.py`.

Re-derive each, do not re-point it. A test re-aimed at the new value certifies
whatever was built — `a-test-can-be-green-on-the-defect`.

## Release fragment

Phase 1 is user-observable (latency, and a new verb). `type: "change"`.
Phase 3 likewise. This bundle alone is docs — `type: "none"` with a reason.
