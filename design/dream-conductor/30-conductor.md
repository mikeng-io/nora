---
type: design
title: "Conductor — read the change once, plan in types, dispatch in code"
description: "The session's three steps: comprehend the pending change into a typed delta; plan which specialists it needs (deterministic wants, then one bounded model call that may prune, order and focus but never invent); dispatch the specialists from code. Why Strands' Swarm, Graph and agent-as-tool are not the dispatcher."
tags: [nora, dreamer, conductor, planning, strands, multiagent]
timestamp: '2026-09-27'
edges:
  - {dst: 40-specialists, rel: depends_on, provenance: asserted}
---

# 30 · Conductor

One session per unit per wake ([20](20-trigger.md)). Three steps, in the
spine's order: comprehend, measure, serialize.

## 1. Comprehend: the delta, once

Code builds one **`DreamDelta`** from the pending set:

- the new human messages per room, as the rows ingest stored (ids, author,
  time, reply target), each with its age rendered;
- the cues ingest scored, per message;
- the live notes, memos and gatherings those messages cite, reply to, edit or
  delete;
- the time events due ([20](20-trigger.md) §3);
- the unit's clock (now, in HK) and the age of the last session.

It is built once and every later step computes on it. Today each adaptor's
`inputs()` re-reads the batch from its own watermark, so note-candidates and
culture read the same messages separately; the delta ends that.

## 2. Measure: the plan

**Step 2a, deterministic.** Each specialist declares
`wants(delta) -> Want | None` ([40](40-specialists.md) §1): a cheap check in
code that says "this change may concern me" and names the keys (a room, a note
id, a gathering id). It generalises the current rule that an adaptor whose
`inputs()` renders `""` costs no model call. If no specialist wants the
delta, the session ends with zero tokens.

**Step 2b, one model call, bounded.** When two or more specialists want the
delta, a **conductor** turn reads the delta and the wanted set and returns a
**plan** through one terminal tool call:

```
plan(jobs: [{specialist, key, focus: [message or row ids], why}], skip: [{specialist, key, why}])
```

The plan may **prune** (the note pass wanted it, the conductor sees chatter),
**order** (the event pass before proactive), and **focus** (hand the memo pass
two message ids instead of forty). It may **not add** a specialist that did
not want the delta, name a key outside the delta, or pass any free text to a
specialist. `why` is logged to the trace and the shared decision log (DEC-179)
and read by nobody's model. A plan that breaks a rule is refused and the
session falls back to 2a's wanted set in the default order: fail-closed means
"do everything code thought relevant", never "do nothing".

The conductor holds read-only tools (`note_read`, `load_transcript`,
`list_events`) under a small cap, so it can look before it prunes. It holds no
write and no act verb.

**Default order** when the plan gives none: memo → note-candidates → event →
note-merge → note-curation → culture → proactive. Writers of the unit's state
before its readers; proactive last because it decides over what the others
just wrote.

## 3. Serialize: dispatch in code

Code runs each job through the existing adaptor run path (`DreamerRuntime.run`
in `nora/agent/dreamer.py`): the specialist's own `inputs()` renders its prompt
from the stores plus the plan's `focus` ids, its own grant and `DreamGuard`
budget apply, its `accept` and `commit` write. Jobs run in plan order; two
jobs on disjoint keys may run concurrently under the session's bound. A job's
failure is its own: the rest of the plan runs, and the failed job's changes
stay pending for the next session.

The plan and every job's outcome are one Langfuse trace per session
(`dream.session` → `dream.plan` → `dream.job:<specialist>:<key>`), so a
session is re-derivable from its trace.

## 4. Why the Strands multi-agent primitives are not the dispatcher

The operator's direction is the Strands Agents SDK, and the conductor and
every specialist ARE Strands `Agent`s, as every pass is today
(`nora/agent/factory.py`). The question is only what connects them.
Read from strands-agents 1.50.2:

| primitive | how the next agent gets its input | fit |
|---|---|---|
| `Swarm` (`strands/multiagent/swarm.py`) | the previous agent calls `handoff_to_agent(agent_name, message, context)`; the next reads that message and a `SharedContext` | **no**: a model writes another model's input |
| `Graph` (`strands/multiagent/graph.py`) | `_build_node_input` concatenates the original task and every predecessor's output text into the node's input | **no, as shipped**: same leak; conditional edges over `invocation_state` are useful |
| `Agent.as_tool()` (`strands/agent/_agent_as_tool.py`) | one `input: str` argument the caller writes; returns text | **no**: the conductor's prose becomes the specialist's prompt |
| code dispatch of a typed plan | the specialist's own `inputs()` from the stores, plus validated ids | **yes** |

All three SDK primitives pass **text a model wrote** into the next model's
prompt. Every rule this tier keeps rests on the opposite: a specialist reads
the record, never a peer's summary of it (a summary is a claim with no
citation, and the next pass would cite it). So the conductor emits types and
code dispatches. `Graph` stays an open option ([50](50-migration.md) §3): a
custom node whose input builder renders from the stores instead of from
predecessors' text would give its parallelism and resume without the leak.

## 5. What the conductor is not

- Not a judge. It never grades a specialist's output; each specialist's
  `accept` does, in code, and the eval suites do after the fact.
- Not a speaker. It holds no act verb. Only proactive holds `remind`, and only
  the fire path speaks.
- Not a cache of state. It keeps no memory between sessions beyond the unit
  watermark and the shared decision log.
