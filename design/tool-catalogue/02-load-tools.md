# 02 — `load_tools` (invocation)

Realizes `00-overview.md`. Covers category 2 only: *what arguments, which arm,
what format?*

## The verb

```
load_tools(names: list[str])  # max 4 names per call
    -> {status, loaded: [...], already_loaded: [...], unknown: [...], refused: [...]}
```

**`names` is capped at 4 per call.** `budget: 3` caps *calls*, not *tokens* —
without a per-call cap one `load_tools(["a"..."t"])` fetches ~8,000 tokens and
undoes the whole change in a single line. Names past the cap come back in
`refused`, and `refused` is not an error: the tools that fit are registered
and usable. 4 covers p90 (2 cold tools) with headroom; max observed is 7,
which is two calls.

Takes names from the catalogue and registers those tools on the running agent;
the **next** model call carries their schemas natively (§"The mechanism").
Nora then calls the tool in the normal way. The result reports names, never
schemas — a schema in the result body would be paid twice, once there and once
in the `tools` array.

It is a **fetch**, not a search. At 54 tools a resident index beats retrieval
on every axis that matters here: it cannot miss, it needs no embedding store
or BM25 index, it has no relevance threshold to tune, and its failure mode is
an error rather than a silent omission. Anthropic's regex/BM25 search exists
for catalogues of hundreds to thousands; the same page's own guidance is that
tool search is for aggregating "200+ tools". Nora has 54 and a catalogue that
fits in 991 tokens.

Accepting a **list** rather than one name matters: 13.6% of turns need two
cold tools and 8.8% need three or more (`00-overview.md`). One call for the
set costs one round trip; one call per tool costs three.

## A turn, end to end

> 「銅鑼灣附近有冇車位」

| call | prompt carries | model does |
|---|---|---|
| 1 | doctrine + catalogue + hot 6 (~11,900) | reads `find_parking — car parks near a place the group names, with live vacancy`; calls `load_tools(["find_parking"])` |
| 2 | + `find_parking` schema (540) | calls `find_parking("銅鑼灣")` |
| 3 | + result + its reporting rules (`03`) | calls `send_reply` |

**Whether this is 3 calls or 4 is ASSUMED, not measured, and it is the
bundle's biggest open cost.** A tag turn spends ~3 today (trace `836eda48…`:
3 `chat` generations at 35,473 / 35,609 / 35,675, each productive). The
absorption argument is that mean distinct tools per turn is 2.02, so a turn
with one cold tool plausibly has a call to spare. But today's call 1 calls the
*actual* tool; here it calls `load_tools` and everything shifts down one.

If the turn grows to 4 calls on cold paths (72.8% of turns), each extra call
carries the full ~17,800-token resident prompt. **Per-turn, that is a ~30–35%
saving rather than the ~50% the per-call table shows.** Still a large win, and
it does not move the break-even anywhere near the observed maximum of 7 — but
the headline number is per-call and the honest unit is per-turn.
`05-measurement.md` §3 measures calls-per-turn and tokens-per-turn directly;
no gate passes on per-call tokens alone.

A hot-tool turn is **unchanged** — 55.2% of calls, no `load_tools` at all.

## Declarations

`NativeTool` requires each field with no default (DEC-133's construction), so
each is stated rather than inherited:

| field | value | why |
|---|---|---|
| `budget` | 3 | sized on measured cold-tool demand: p90 is 2 distinct cold tools, max 7. Three calls covers p90 with one retry after an `unknown`. Not sized to the max — a turn needing 7 distinct cold tools is 0.2% and can spend two `load_tools` calls. |
| `terminal` | `False` | it ends nothing. Only `send_reply` is terminal. |
| `time_shiftable` | `None` | no time dimension. |
| `read_only` | `True` | it reads the registry. It must be in the Dreamer's ceiling or the Dreamer cannot reach its own deferred tools. |
| `source_tier` | `None` | not a fact source — it is registry plumbing, and it must stay out of the sourcing nudge. |
| `plugin` | `False` | built by `build_tool_specs` like every other tool, closed over a per-turn `ToolShelf` (`None` → `unavailable`, the codebase's usual unwired shape). |
| `output_contract` | unset | a `three_state` read-only tool counts as a LOOKUP for R-97 (`lookup_tools()`), and «did I load a schema» is not «did this turn come back with something». |

**`budget` interacts with `tool_calls`.** Per the existing measurement, a
per-tool budget is inert under a shared pass ceiling — `max_memory_calls`
binds a `MEMORY_TOOLS` verb before `tool_calls` does. `load_tools` consumes
from the shared ceiling like any verb, so the ceiling must rise by the
expected `load_tools` count (mean 0.8/turn) or the change silently costs a
real tool call. `04-code-surface.md` §5.

## Result shape

```json
{
  "status": "ok",
  "loaded": ["find_parking"],
  "already_loaded": [],
  "unknown": [],
  "refused": []
}
```

*Corrected 2026-09-23 while building phase B:* the draft shape returned each
tool's `description`/`inputSchema` in `tools`. That belonged to the abandoned
schemas-as-text mechanism; under `register_dynamic_tool` it would pay every
fetched schema twice.

Three statuses, R-27's shape:

- **`ok`** — at least one name is usable: in `loaded`, or `already_loaded`
  (resident, or loaded earlier this turn — `register_dynamic_tool` raises on a
  duplicate, so this is checked first and is not an error).
- **`nothing_found`** — no name resolved. `unknown` lists them. The catalogue
  is in front of her, so this means she invented a name or mistyped one; the
  correct next act is to re-read the catalogue, not to retry the same string.
- **`unavailable`** — the registry could not be read. Distinct from
  `nothing_found` by conduct.md's own rule, which this tool must not be the
  first thing in the system to blur.

A **partial** resolve is `ok` with a non-empty `unknown` or `refused`: the
tools that resolved are registered and usable, and reporting that as a failure
would throw away work that succeeded. `refused` means over the 4-name cap —
say which, so the next call asks for the rest rather than re-asking for all.

## Failure modes, and why this one fails safely

**She never calls it.** The DEC-28 trap, and the real risk. DEC-28's own
lesson is that this is hard to observe — no tool logged its own name, so a
grep returned zero whether the tool ran every turn or never. The observable
that can tell them apart is the metric, and this bundle adds
`nora_tool_calls_total{tool="load_tools"}` plus a counter for *turns that
ended without a reply while a cold capability was named in the room*
(`05-measurement.md` §3). Measured by the eval suite before shipping, not
inferred from a log.

**She invents a tool name.** R-7 default-deny already vetoes an unregistered
name with no edit to `guards.py` — the property DEC-10 exists for. The
catalogue narrows this further by putting every real name in front of her.

**She loads a tool and does not use it.** Costs that schema's tokens for the
rest of the turn. Bounded by `budget: 3` and by the catalogue being specific
enough to pick correctly, which is `01`'s discriminator rule.

**She calls `load_tools` for a hot tool.** Wasted round trip. The catalogue
marks the hot six with `*` and the header says they are already loaded. If
measurement shows this happening, the marker is the thing to fix.

## Persistence within a turn

**Loaded schemas persist for the rest of the turn** — concretely, they stay in
`dynamic_tools` until the turn ends. A turn is bounded (~3 model calls,
`tool_calls` ceiling), cold tools per turn are median 1 / p90 2, and
re-fetching would spend a round trip to save ~407 tokens.

**The registry that holds `dynamic_tools` must be per-turn, not shared.**
*Corrected 2026-09-23:* an earlier version of this paragraph said Nora's
`ToolRegistry` "is constructed per turn by `build()`". It is not — its own
docstring says it is built **once per process** by the composition root, and
it holds process-lifetime collaborators (the transit catalogue, the vision
cache). The object that must be per-turn is the **Strands agent's**
`tool_registry`, which is where `dynamic_tools` lives — and it is: every loop
builds a fresh `Agent` per turn (`factory.create_agent` takes that turn's
`pre_fill`; `create_dream_agent` is built per run). The shelf `load_tools`
loads from is also per-turn by construction — `ToolRegistry.surface()` creates
it. A shared registry would leak tool availability between concurrent rooms
and silently regrow the resident set toward 22,130 while every per-call
number kept looking fine; `05` §3 still watches resident size as a gauge.

Across turns nothing persists: each turn builds its own prompt and its own
registry.

## The mechanism — `register_dynamic_tool`, verified

**Strands 1.50.2 has a first-class deferred-loading path.** Verified by
reading the installed package and by a spike (`05` §2b):

| seam | file | what it does |
|---|---|---|
| `ToolRegistry.dynamic_tools` | `tools/registry.py:39` | a second dict beside `registry` |
| `register_dynamic_tool(tool)` | `tools/registry.py:562` | "Register a tool dynamically for temporary use" |
| `get_all_tools_config()` | `tools/registry.py:219` | merges `registry` + `dynamic_tools` |
| `get_all_tool_specs()` | `event_loop.py:152`, `:523` | **re-read on every model call in the loop**, not once at construction |
| dispatch | `tools/executors/_executor.py:142` | checks `dynamic_tools` **first**, then `registry` |

So `load_tools` registers the fetched tools on the agent's own registry, and
the **next model call in the same loop sees them in its `tools` array
natively**. The model is never asked to call something it has not been given;
the framework is never asked to dispatch something it does not hold.

An earlier draft of this file proposed returning schemas as text in the
`tool_result` body specifically to avoid mutating the tools array. That was
solving a problem this stack does not have, and it would have required the
model to emit a `tool_call` for an undeclared tool and Strands to dispatch it
— `_executor.py:449` returns `"Unknown tool: {name}"` for exactly that case.
**The draft mechanism would have dead-ended at the framework.**

## Interaction with prompt caching — this is where the real cost is

Today 30,080 of 35,473 tokens are cached, and a cold call costs 4,575ms
against ~1,600ms warm.

Because `get_all_tool_specs()` is re-read per call, **the tools array changes
on the call immediately after a `load_tools`** — and on an OpenAI-compatible
API `tools` is rendered into the prefix ahead of the messages. So that call
takes a **full prefix miss**, not a partial one.

Per cold turn, expected shape:

| call | tools array | cache |
|---|---|---|
| 1 (`load_tools`) | hot 6 + `load_tools` — identical every turn | **hit** |
| 2 (the fetched tool) | + the fetched schema — new | **miss** |
| 3 (`send_reply`) | unchanged from call 2 | hit off call 2 |

One full miss per cold turn, on 72.8% of turns. That is a real cost the
earlier draft claimed to avoid and does not.

It is still a large net win, because the absolute number being missed is
~18,000 rather than ~35,500 — but the honest claim is "the resident prefix is
stable and smaller", **not** "the cached prefix is preserved". `05` §2e
measures it instead of asserting it.

## Known interaction: #325

OmniRoute's Chat Completions path drops reasoning on multi-turn (#325, open).
`load_tools` adds a turn on cold paths. If post-change measurements show
sample loss rather than quality loss, suspect #325 first.

## One builder for every loop — `ToolRegistry.surface()` (phase B, built)

Operator direction, 2026-09-23: the tag path, participation, proactive act,
reminders and every Dreamer adaptor use **the same** builder — not one each.

Before phase B, «which tools does this occasion see» was decided in three
places: the Discord-only music cut and the Dreamer-only `search_memory_workspace`
gate (#350) inside `build_tool_specs`, the MCP allowlist inside `build()`, and
a name filter `RegistryToolSource` ran over `build()`'s output. Now:

| object | module | what it is |
|---|---|---|
| `ToolScope` | `nora/agent/surface.py` | value object — which tools an occasion may see: platform, whether the Dreamer built it, an optional `grant` ceiling |
| `ToolShelf` | `nora/agent/surface.py` | per-turn holder of the deferred tools `load_tools` fetches from |
| `ToolSurface` | `nora/agent/surface.py` | the result: `tools` for `Agent(tools=)`, `resident`, `shelf`, `admitted`, and a lazily rendered `catalogue` |
| `ToolRegistry.surface()` | `nora/agent/registry.py` | the one builder; `build()` is `surface(...).tools` |

| loop | call site | scope |
|---|---|---|
| tag / engagement | `nora/engagement/pipeline.py` | the room's platform; `allowed_mcp` |
| participation, proactive act, reminders | `nora/app/fire.py` (`FirePath`) | the room's platform |
| every Dreamer adaptor | `nora/agent/dreamer.py` (`RegistryToolSource`) | `grant` = the adaptor's tools ∩ (read-only ∪ granted acts); `dreamer=True` |
| relevance gate | — | holds no tools. `capability_digest()` shares the line renderer; still **off** (see below) |

**Invariant:** the catalogue, the resident set and the shelf are all derived
from ONE admitted set. The catalogue cannot name a tool the turn cannot load
— which a catalogue rendered from the declared table would, in every Signal
room, for the six music verbs.

**The relevance gate stays off.** Handing the gate this surface (#144) was
measured on the 40-case suite and reversed on 2026-09-10: false NO_GO 3 → 5, no
capability improved, +707 tokens per quiet window. It shares the renderer so
there is one source; switching it on needs that suite re-run first.

Phase B ships **inert**: every caller passes `defer=False`, so every turn is
handed exactly the tools it was before and `load_tools` is never in the
array. `tests/unit/test_tool_surface.py` pins that, and drives a real Strands
loop to show a shelved tool absent on call 1, offered on call 2, dispatched.
