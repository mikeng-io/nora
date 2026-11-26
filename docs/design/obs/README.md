---
class: design
subject: observability, the evidence a claim about a turn is re-derived from
owns:
  - trace roots own their stages: "one root span per entry point, its stages and actions its children; spawned work attaches its intended context explicitly"
  - trace content boundary: "a trace carries a model call's full input and output, to a sink the operator owns, and nowhere else"
area: obs
links:
  - {to: docs/design/cognition/execution.md, rel: uses}
  - {to: docs/design/cognition/boundaries.md, rel: uses}
  - {to: docs/operations/observability.md, rel: uses}
---

# Observability

Nora's claims about what she did are only as good as what can be read back. The
trace of a turn is the evidence for every statement about it: what the model
saw, which tools ran, what was delivered, and under which doctrine. Nothing
here is cognition state: observability records what the loops did and decides
nothing in them.

How the pieces are deployed, queried and recovered is in
[operations](../../operations/observability.md).

## Parts

| part | answers |
|---|---|
| **traces** | what happened in one pass, in order, and what each model call saw and said |
| **metrics** | how often, how long and how many, across passes |
| **logs** | what the process said where no span applies |

## Roots and stages

Every entry point has exactly one **root span**: a pass of either loop
([execution](../cognition/execution.md)), an ingest job, a reading, a deferred
delivery. Its stages and actions are its children, so a trace has one shape
whatever started it.

Spawned work does not inherit its caller's context. A task that outlives its
caller, or whose duration would hold the caller's trace open, **attaches the
context it intends**: to the root it belongs under, or to its own root. A
detached job that makes model calls opens its own root and declares it holds
one; it is not dropped for being detached.

A root declares what it holds. A root with a model call under it is kept as a
trace; a root with none is counted, not kept. A job mislabelled as the second
disappears from the evidence while its counters say it ran.

Provenance on a span is ids and hashes only: the pass, the room, the doctrine
revision. No content rides in an attribute that is meant only to identify.

## What a trace carries

A trace carries each model call's full input and output, including the room
text and the injected context, and the arguments and results of tools. Debugging
and evaluation need to read them, and a reading that cannot be reproduced from
its trace is not evidence.

- The sink is one the operator owns and operates, on the host. The disclosure
  boundary is whoever can read the sink: the same people who can read the record
  those messages already sit in.
- Nothing is exported off the host. A sink somewhere else is a new disclosure
  that needs its own decision; nothing in the code would notice that change on
  its own.
- Scope is enforced when a pass is assembled, never when a span is exported
  ([boundaries](../cognition/boundaries.md)). By the time a span exists, the
  disclosure it records was already authorised, so the guards that bound what
  reaches a room do not bound what reaches a trace.
- Withholding content is a decision about fields: transcript, tool results and
  the doctrine are different disclosures, and a switch that cannot tell them
  apart removes the evidence along with the exposure.

## Instruments

- Every instrument keeps its three outcomes apart, as the anchor requires
  ([execution](../cognition/execution.md)): a wiring gap or a failed read is
  unavailable, never an empty result.
- A metric is declared once, has a call site, and takes its labels from the
  code that emits it. A declared series nothing can increment is a lie.
- Every zero is paired with a positive control, so a count of nothing can be
  told from an instrument that is off.
- The cost of a stage is measured as well as its result, and a log line is not
  a metric.

## Build identity

What version she is running is answered by [agent](../agent/version.md).
