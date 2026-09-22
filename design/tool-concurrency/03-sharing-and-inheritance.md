---
type: design
title: "Sharing and inheritance — one limit, held by the parent, inherited by children"
description: "The operator asked for the limit to be both shareable and inheritable. A contextvar set at agent construction carries the parent's limit to sub-agents, which inherit rather than restate."
tags: [nora, agent, concurrency, subagents, contextvars, config]
timestamp: '2026-09-23'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 02-the-ceiling, rel: depends_on, provenance: asserted}
---

# Sharing and inheritance

## 1. The requirement, in the operator's words

> 「當然 per tools 我唔使，我覺得係所有 tools。我覺得兩樣啦：1. 一個係 shareable，
> 一個係即係 inherit 囉可以 2. 第二當然佢係要獨立 bundle」

Two properties, both about the *limit* rather than the executor:

- **Shareable** — one limit governs all tools. Not `media_fetch: 4` plus
  `web_search: 4`; one number.
- **Inheritable** — a sub-agent does not restate the limit. It runs under its
  parent's.

These are different mechanisms and the distinction matters. "Shared" is about
scope *across tools*; "inherited" is about scope *across the agent tree*. A design
that only gets the first is a config value read at each site; one that gets the
second is a value that **propagates**.

## 2. Who is at risk

> **⚠️ CORRECTED 2026-09-23** — the first version listed **`debrief`** as an
> at-risk sub-agent. **It is not.** `debrief` runs through `ask_text`, which builds
> `Agent(tools=[])` (`nora/agent/structured.py:49`, `:172`) — an agent with no tools
> cannot emit a tool call, so it adds nothing to the exposure surface.
> (06-review-round-1 §m2.) The critic/author claims survive, since those run
> through the dream adaptors. The tree this has to cross

```bash
$ grep -nE "^def create_|^async def create_" nora/agent/factory.py
333:def create_agent(          # tag turns
438:def create_dream_agent(    # dream runs
```

Sub-agents observed in the traces (by `invoke_agent <name>` spans) include
`debrief`, `author`, `accuracy_critic`, `argument_critic`, `dreamer:culture`,
`dreamer:fact-adjudication`. A few reach large batches — `dreamer:fact-adjudication`
reached 16, `accuracy_critic` 11, `author` 9 — so the ceiling has to hold on those
paths too, and they are constructed from a different call site than the tag turn.

## 3. Inheritance: a contextvar, set once at the parent

The precedent is already in the repo and is the reason this is a small change
rather than a plumbing exercise. `nora/obs/tracing.py:288` documents it:

> **This exists because `asyncio.create_task` DOES copy contextvars**, not …

That is exactly the mechanism needed here: `asyncio.create_task` copies the
current context into the new task, so a value bound at the parent is visible in
every task it spawns — and Strands' executor *is* `create_task`
(`concurrent.py:56`). The same property that makes `tag_id` ride along on every
log record (`nora/obs/logging.py:142-146`, via `structlog.contextvars`) carries the
concurrency limit down the tree.

So:

```python
# at agent construction (the one place the limit is READ)
tokens = _CONCURRENCY_LIMIT.set(cfg.agent.max_concurrent_tools)

# the executor reads the CURRENT value, not a captured one
limit = _CONCURRENCY_LIMIT.get()
```

> **⚠️ PARTLY CORRECTED 2026-09-23 — two problems.** (06-review-round-1 §M2,
> §m6.)
>
> **(1) This contradicts §02.** `02`'s constructor takes `max_concurrent: int` — a
> threaded parameter. This section says the executor reads the *current* contextvar.
> Both cannot be true. **The resolution is that they are different layers, and the
> document now says so:** the contextvar is the *inheritance* mechanism (a sub-agent
> sees the parent's value); the constructor argument is the *initial* value a
> top-level agent is built with. An implementer must wire the first to the second.
>
> **(2) The “no signature change” claim is FALSE for a top-level agent.** This
> repo's contracts forbid reaching for config below the composition layer
> (`factory.py:18-19` “Takes explicit scalars, never `nora.config` objects”;
> `agent_runner.py:113` “no config object below”). A process-startup `.set()` does
> not reach a per-turn task, so the limit **must** arrive at the per-turn `set()` site
> as a threaded scalar — i.e. exactly the signature change this section claimed to
> avoid. The contextvar buys inheritance *downward* (parent → sub-agent), it does
> **not** buy a change-free top-level path. The real `set()` site must be named in
> the build.
>
> **(3) Today it is prophylactic, not load-bearing** (§m6): tag and dream roots are
> top-level loops, and the only agents built inside a turn's context are
> `tools=[]` agents that cannot emit tool calls. So the inheritance property has no
> production edge to exercise yet; the config default alone delivers identical
> behaviour today. Stated so the operator does not over-read its immediate effect.

A sub-agent built inside the parent's task reads the parent's value without a
single parameter being threaded through `create_agent` / `create_dream_agent` — no
signature change, no new required argument at any call site, and no way for a new
sub-agent to silently forget it. Defaulting the contextvar to the configured value
means a construction path that never sets it still gets the right bound rather
than an unbounded executor.

**This is the property the operator asked for stated as a rule: a child inherits;
it does not restate.** An explicit parameter would be restating — every new call
site becomes a place the limit can be wrong, and the failure is silent (the
executor simply runs unbounded).

## 4. Where the limit is configured

One key, one place. The repo's config convention requires every key to appear in
the template with a comment, so this lands as:

```toml
[agent]
# The ceiling on tool calls running AT ONCE in one turn. Distinct from the
# per-tool budget in NATIVE_TOOL_BUDGETS: that bounds how many calls a tool may
# MAKE; this bounds how many RUN SIMULTANEOUSLY. Default 4 matches the
# media_fetch budget, so a post with four videos is not degraded by the bound
# that protects it.
max_concurrent_tools = 4
```

**Why `[agent]` and not `[media]`:** the operator's requirement is all tools, and
the hole is in the executor, not the media lane. A `[media]`-scoped key would
re-introduce the framing this bundle exists to correct.

## 5. What "independent bundle" means for the boundary

The operator asked for this to be its own bundle («佢係要獨立 bundle»), and that
has a concrete consequence for scope:

- This bundle owns **the executor, the limit, and its propagation** — a
  framework-layer concern touching any tool.
- `design/media-read-lane/` owns **the media lane's own numbers** — the
  per-invocation timeout arithmetic (`04 §3a`), the chunk size, the provider lane.
- The overlap is exactly one sentence, and it belongs here: media-read-lane's
  deferred list already carries *"Concurrent `media_fetch` tool calls inside one
  agent turn (whether Strands parallelises multiple tool_use blocks is UNTESTED)"*.
  **This bundle answers that question** — they do parallelise, and here is the
  ceiling — so that deferred item should be amended to point here rather than
  (**already DONE — the media branch amended it in `ee2855c8`; this is a verify,
  not an assign. Flagged 06-review-round-1 §m3.**)
  left as an open question. That amendment is a one-line edit in the other
  bundle, listed as a delivery obligation in 05 §3, not a merge of the two.

## 6. Not inherited: which pool a call draws from

Stated to prevent an over-read. The contextvar carries a **number**, not a shared
executor object. Each `Agent` still constructs its own executor and its own
semaphore. A parent and its sub-agent therefore have *separate* pools of the same
size, not one shared pool.

That is the intended reading of "inheritable" (the child uses the parent's
*policy*), but it is worth being explicit, because "shareable" could be read as
"one global pool". A single global pool would be a different — and worse — design:
it would make a dream run compete with a live tag turn for slots, so a background
job could delay a user-facing reply. If a genuinely global bound is wanted later,
that is a separate decision with its own tradeoff, and it is deferred (05 §4).
