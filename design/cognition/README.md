# Cognition

Nora's cognition is one graph worked by two open loops. The **Cognitive
Graph** is her state. The **engagement loop** is Nora in a room; the
**reflection loop** is Nora between conversations. Both share the OODAS stage
model, and each runs a declared subset of its stages. The goal is **emergence**: recommendation, personalisation,
follow-through and learning arise from the generic loops working the graph,
never from a capability's own code, store, prompt or loop.

**Status:** designed

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Fcognition%2F%22)

## What Nora is

| part | is | defined in |
|---|---|---|
| **graph** | the state: rows and edges, each with a basis, a scope and provenance | [graph](graph.md), [claims](claims.md), [storage](storage.md) |
| **loops** | the process: the engagement and reflection loops, each running the OODAS stages as passes | [loops](loop.md), [context](context.md), [observation](observation.md), [retrieval](retrieval.md), [surface](surface.md) |
| **executors** | the runtimes that run passes and actions | [execution](execution.md) |
| **doctrine** | authored law, seed and practice, outside the graph | [boundaries](boundaries.md) |

How she improves is [learning](learning.md); why no capability gets its own
part is [emergence](emergence.md).

## Subjects

| file | defines |
|---|---|
| [graph](graph.md) | basis and scope, the planes, five kinds, intentions, edges, presentations |
| [claims](claims.md) | profile parts, support and precedence, likes and constraints, disputes and decay |
| [storage](storage.md) | per-kind tables with shared planes and one edge table, as the truth |
| [loops](loop.md) | OODAS, the engagement and reflection loops, actions, passes, the debrief, what keeps them open |
| [context](context.md) | the four context layers and the presentation blocks for both loops |
| [observation](observation.md) | sources, aggregates, stimuli |
| [retrieval](retrieval.md) | one read semantics for prefetch and read tools |
| [surface](surface.md) | the instruction, tools per pass, admission |
| [execution](execution.md) | executors, waking, replay, time, settlement, tracing |
| [boundaries](boundaries.md) | authority of authored text, scope, egress |
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
- The unit is the read boundary; only the engagement loop delivers, through
  the guards.
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
- **"Cognitive Graph", not "knowledge graph"**: most of it is belief,
  commitment and outcome, not knowledge.
