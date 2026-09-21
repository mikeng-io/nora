# 02 — `load_tools` (invocation)

Realizes `00-overview.md`. Covers category 2 only: *what arguments, which arm,
what format?*

## The verb

```
load_tools(names: list[str]) -> {status, tools: [...], unknown: [...]}
```

Takes names from the catalogue, returns their full schemas into the turn.
Nora then calls the tool in the normal way.

It is a **fetch**, not a search. At 54 tools a resident index beats retrieval
on every axis that matters here: it cannot miss, it needs no embedding store
or BM25 index, it has no relevance threshold to tune, and its failure mode is
an error rather than a silent omission. Anthropic's regex/BM25 search exists
for catalogues of hundreds to thousands; the same page's own guidance is that
tool search is for aggregating "200+ tools". Nora has 54 and a catalogue that
fits in ~900 tokens.

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

**Three model calls — the same as today.** A tag turn already spends ~3
(measured: trace `836eda48…` ran 3 `chat` generations at 35,473 / 35,609 /
35,675). Today's call 1 spends a round trip on a tool anyway; here it spends
it on `load_tools`. The extra trip is mostly absorbed, not added.

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
| `plugin` | `False` | built and described by this registry. |

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
  "tools":   [ {"name": "find_parking", "description": "...", "inputSchema": {...}} ],
  "unknown": []
}
```

Three statuses, R-27's shape:

- **`ok`** — every name resolved; `tools` carries their schemas.
- **`nothing_found`** — no name resolved. `unknown` lists them. The catalogue
  is in front of her, so this means she invented a name or mistyped one; the
  correct next act is to re-read the catalogue, not to retry the same string.
- **`unavailable`** — the registry could not be read. Distinct from
  `nothing_found` by conduct.md's own rule, which this tool must not be the
  first thing in the system to blur.

A **partial** resolve is `ok` with a non-empty `unknown`: the tools that
resolved are usable, and reporting that as a failure would throw away work
that succeeded.

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

**Loaded schemas persist for the rest of the turn.** A turn is bounded (~3
model calls, `tool_calls` ceiling), cold tools per turn are median 1 / p90 2,
and re-fetching would spend a round trip to save ~407 tokens on a call that is
already the cheap one. AgentCore's plugin clears tools between *invocations*,
not within one — the analogous boundary here is the turn.

Across turns nothing persists: each turn builds its own prompt.

## Interaction with prompt caching

Today 30,080 of 35,473 tokens are cached, and the cold call costs 4,575ms
against ~1,600ms warm. The cache is the reason this is a first-call latency
and money problem rather than a per-call one, and it is the thing most likely
to be damaged by accident.

Under this split the resident prefix (doctrine + catalogue + hot set, ~11,900)
is **stable across every turn**, which should cache at least as well as
today's. Calls *after* a `load_tools` carry a tool array that varies by which
tools were fetched, and may miss.

Anthropic's server-side implementation avoids this by keeping deferred tools
out of the system-prompt prefix and appending discovered definitions inline in
the conversation. A client-side implementation on OpenAI Chat Completions
should do the same where the provider allows it: return the schemas **in the
`tool_result` body**, not by mutating the `tools` array, so the cached prefix
is untouched. Whether DeepSeek via OmniRoute will then emit a `tool_call` for
a tool absent from the `tools` array is **unverified and is the single
highest-risk unknown in this bundle** — `05-measurement.md` §2b probes it
before anything else is built, and a negative result forces the
array-mutation variant and a cache re-measurement.

## Known interaction: #325

OmniRoute's Chat Completions path drops reasoning on multi-turn (#325, open).
`load_tools` adds a turn on cold paths. If post-change measurements show
sample loss rather than quality loss, suspect #325 first.
