# Cognition

Nora's cognition is one open loop over one graph. The **Cognitive Graph** is
her state: what happened, what code derived from it, what she believes and
what she has committed to. The **Cognition Loop** is the process that reads and
writes it. The goal is **emergence**: recommendation, personalisation,
follow-through and learning arise from the generic loop working the graph, not
from a feature's own code, store, prompt or loop.

**Status:** designed

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Fcognition%2F%22)

## Parts

- **[The graph](graph.md)**: two axes every row carries (basis and scope), the
  planes shared by every row, five kinds, typed edges and the read-time
  presentations (profile, Standing, lessons, likes).
- **[The loop](loop.md)**: OODAS — each stage's question, actions, actor and
  rules; the five passes (called, ambient, proactive, subconscious, review);
  the open loop's stimuli, watches and bounds.
- **[Observation](observation.md)**: sources, aggregates and stimuli.
- **[Execution](execution.md)**: executors, waking, time, settlement and
  tracing.
- **[Retrieval](retrieval.md)**: one read semantics for prefetch and read
  tools.
- **[The model's surface](surface.md)**: instruction, tools per pass, and
  admission.
- **[Boundaries](boundaries.md)**: authority of authored text, scope and egress.
- **[Claims](claims.md)**: profile parts, support and precedence, likes and
  constraints, disputes, deletion and decay.
- **[Storage](storage.md)**: how the graph is held — per-kind tables with shared
  planes and one edge table; the tables are the truth.
- **[Learning](learning.md)**: how an outcome of Nora's own act changes a later
  decision, and how every link of that chain lives in the graph.
- **[Emergence](emergence.md)**: closed form over open content; code computes
  form, never meaning; capabilities worked through the generic loop; the
  evidence that tells emergence from drift.

## The shape

| | is | is not |
|---|---|---|
| **the graph** | the state: rows and edges with basis, scope and provenance | a projection of other stores, or one of several memories |
| **the loop** | the process: OODAS, run as passes | a store, or a set of per-feature engines |
| **executors** | the runtimes that run a pass or a step | stages; an executor may run several |
| **doctrine** | authored law, read by the loop | a row in the graph; nothing the loop writes outranks it |

Nora is the graph, the loop and her doctrine. Once the graph is defined, the
loop follows from it on both sides: the model's side is the instruction and
tool surface that state what each kind means and offer each kind's propose
verbs; the code's side is admission, computation and delivery, which follow
from each kind's basis and lifecycle.

## Invariants

- Every row carries one **basis** and one **scope**. There is no other
  classifying axis.
- Each stage writes only its own basis. Study writes two, from two different
  writers.
- The model proposes every inferred and committed row; code admits it. Code
  never decides what something means; the model never decides what is admitted.
- An inferred row cites only observed or computed rows. Belief cannot become
  the root of its own support.
- An act counts only once observe has recorded it. Only a recorded outcome
  closes an intention.
- Code names the form and never the content: no capability has its own code
  path, store, prompt section or loop.
- The unit is the read boundary. Delivery to a room runs through the conscious
  fire path and its disclosure and send guards; Cognition has no delivery verb.
- An unavailable read is unknown, never absence. A failed or bounded step fails
  closed and leaves its input pending.

## Scope

This bundle defines the form that the [Cognition session](../cognition-loop/session.md),
the [memory graph](../unified-memory/graph.md), the Notebook and Register,
Standing, live threads, lessons and [personalisation](../personalisation/README.md)
are expressions of. Where another bundle names a separate store, engine or
classifying axis for one of them, this bundle's kinds, stages and presentations
are the design. Those bundles describe the code that runs today; nothing new is
built to them, and they leave with that code when this design is implemented.

## Why this shape

- **One graph, not a memory graph beside a standing graph.** Nora's beliefs
  and commitments cite the same items and rest on each other; two graphs would
  need edges between them and two writers for one judgement.
- **Basis instead of layers, tiers and memory types.** Each of those classified
  rows by a different question; together they gave one row several overlapping
  names. How a row is proven decides how it is written, checked and trusted, so
  basis is the one axis that does work.
- **Intentions as rows with closing conditions.** A commitment that cannot be
  closed by an observed outcome cannot be verified, and an agent that never
  states what it expects cannot learn from what happened.
- **No per-feature engines.** A framework that encodes how recommendation or
  personalisation works fixes the semantics in code, where Nora cannot learn
  them. Semantics live in claims and lessons, which she revises.
- **No event log as truth.** Canonical rows with revisions and provenance are
  re-derivable; a replayed log of every event adds a second truth to keep
  consistent.
- **"Cognitive Graph", not "knowledge graph".** Most of what it holds is
  belief, commitment and outcome, not knowledge.
