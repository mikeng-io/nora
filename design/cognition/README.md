# Cognition

**Nora treats memory, belief, commitment and outcome as one continuous,
evidence-grounded cognitive state.**

That state is the **Cognitive Graph** — Nora's own term, not an industry
category. Two loops work it: the **engagement loop** is Nora in a room; the
**reflection loop** is Nora between conversations. Both share the OODAS stage
model, and each runs a declared subset of its stages. Each pass is a controlled
transition: from the graph as it stands, new observations and doctrine, it runs
its declared stages and records what was admitted and whether it completed or
failed; an engagement pass also ends in an act or a deliberate silence. The
graph alone does not think or act; cognition is the graph with its loops.

The goal is **emergence**: recommendation, personalisation, follow-through and
learning arise from the generic loops working the graph, never from a
capability's own code, store, prompt or loop.

## What Nora is

| part | is | defined in |
|---|---|---|
| **graph** | the state: rows and edges, each with a basis, a scope and provenance | [graph](graph.md), [claims](claims.md), [storage](storage.md) |
| **loops** | the process: the engagement and reflection loops, each running the OODAS stages as passes | [loops](loop.md), [context](context.md), [observation](observation.md), [retrieval](retrieval.md), [surface](surface.md) |
| **executors** | the runtimes that run passes and actions | [execution](execution.md) |
| **doctrine** | authored law, seed and practice, outside the graph | [boundaries](boundaries.md) |
| **agents** | the Noras of one deployment, each an authored profile serving one circle of units | [boundaries](boundaries.md#agents) |

How she improves is [learning](learning.md); why no capability gets its own
part is [emergence](emergence.md).

## Subjects

| file | defines |
|---|---|
| [graph](graph.md) | basis and scope, the planes, five kinds, intentions, edges, presentations |
| [claims](claims.md) | profile parts, support and precedence, likes and constraints, disputes and decay |
| [acts](acts.md) | the act envelope; delegated work |
| [storage](storage.md) | per-kind tables with shared planes and one edge table, as the truth |
| [loops](loop.md) | OODAS, the engagement and reflection loops, actions, passes, the debrief, what keeps them open |
| [context](context.md) | the four context layers and the presentation blocks for both loops |
| [observation](observation.md) | sources, aggregates, stimuli |
| [retrieval](retrieval.md) | one read semantics for prefetch and read tools |
| [surface](surface.md) | the instruction, tools per pass, admission |
| [execution](execution.md) | executors, waking, replay, time, settlement, tracing |
| [boundaries](boundaries.md) | authority of authored text, agents, scope, egress |
| [learning](learning.md) | verdicts, track records, lessons graded by consequences |
| [emergence](emergence.md) | closed form over open content; capabilities through the generic loops |

## Invariants

- Every row carries one basis and one scope; nothing else classifies it.
- Each stage writes only its own basis; study's two bases have two writers.
- The model proposes inferred and committed rows; code admits them. Code
  never decides meaning; the model never decides admission.
- An inferred row cites only observed or computed rows.
- An act counts only once observe records it; only a recorded outcome closes
  an intention.
- Code names the form, never the content.
- The unit is the read boundary; an affiliate also reads its primary, and
  nothing is read across agents. Only the engagement loop composes deliveries;
  every delivery takes an admitted route through the common guards.
- An unavailable read is unknown, never absence; a failed step fails closed and
  leaves its input pending.

## Scope

This bundle is the design of Nora's cognition. The [Cognition
session](../cognition-loop/session.md), the [memory
graph](../unified-memory/graph.md), the Notebook and Register, the Standing
fold, live threads, lessons and [personalisation](../personalisation/README.md)
are expressions of its kinds, loops and presentations. Those bundles describe
the code that runs today; nothing new is built to them, and they leave with
that code when this design is implemented. [Context
assembly](../context-assembly/README.md) and [evaluation](../evaluation/README.md)
stay their own subjects and follow this one.

## Where it lives in the code

Areas follow [ADR 0037](../../docs/adr/0037-code-is-grouped-by-area-and-depends-inward.md).

| part | owner |
|---|---|
| the graph: registry, entities, claims, intentions, edges, admission, retrieval, presentations | `memory/`, which absorbs the Notebook, Standing, profiles, lessons and the account of herself |
| items | the area of their source: `conversation/`, `location/`, `media/`, `social/`, `computer/`, `voice/` |
| aggregates | `analytics/` for rollups; each area for its own current heads |
| agents, identities, rooms, units: primary and affiliates | `people/` |
| delivery triggers, schedules, watches | `agenda/` |
| delegated work | `subagents/` and `computer/` |
| the engagement loop and the debrief | `engagement/`, with the fire path in `app/` |
| the reflection loop | `dreamer/` |
| context assembly | `agent/` |
| the act envelope | `domain/`, the shared kernel; each area's tools implement it |

## Why this shape

- **One graph**: beliefs and commitments cite the same items and rest on each
  other; two graphs would need edges between them and two writers for one
  judgement.
- **Basis as the proof axis**: how a row is proven decides how it is written,
  checked and trusted; layers, tiers and memory types each classified by a
  different question and gave one row several names.
- **Intentions with closing conditions**: a commitment no recorded outcome can
  close cannot be verified, and an agent that never states what it expects
  cannot learn.
- **No engines per capability**: encoding how recommendation works fixes its
  semantics in code, where Nora cannot learn them.
- **Canonical rows, not an event log**: rows with revisions and provenance are
  re-derivable; a replayed log is a second truth.
- **"Cognitive Graph", not "knowledge graph" or "memory graph"**: a knowledge
  graph manages what is known and a memory graph what is remembered; this
  manages what Nora believes, what she commits to, and how outcomes correct
  her. Its data model has precedents; what is specific is that basis decides
  write authority and that commitments close on observed outcomes.
