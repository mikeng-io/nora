---
class: design
subject: "the agenda: schedules, due deliveries and the kinds a due delivery becomes"
owns:
  - agenda
  - delivery kind
  - due delivery
  - reminder: "the scheduled delivery of an intention to its person or room, put to Nora as a request when it comes due and never a stored message replayed"
  - heartbeat: "the driver that takes each due delivery on a tick and hands it to the fire path; it knows time passed and nothing about why it matters"
  - delivery claim: "the exclusive move of a due delivery from scheduled to claimed, a compare-and-set inside the statement, so two processes never fire one delivery"
  - at-most-once: "a delivery fires once ever: one left claimed by a dead process resolves to failed and is never re-queued, so a lost delivery is visible and a doubled one never happens"
area: agenda
links:
  - {to: docs/design/cognition/loop.md, rel: uses}
  - {to: docs/design/cognition/execution.md, rel: uses}
  - {to: docs/design/cognition/graph.md, rel: uses}
---

# Agenda

The agenda is the part of Nora that knows **when**. It holds the schedules, the
delivery triggers and the watches that make an intention due, and it hands each due
delivery to the fire path. What the pass then does is the
[engagement loop's](../cognition/loop.md): a proactive pass digests the intention
and may deliver, act elsewhere, move its trigger or stay silent.

## What the agenda holds

| part | is |
|---|---|
| **schedule** | the times at which an intention is due: a moment, a recurrence, a review time |
| **delivery trigger** | a time, or a watched condition holding, that makes a chosen-moment delivery due |
| **watch** | an outside source polled on behalf of a maintain intention while it holds ([observation](../cognition/observation.md)) |

A reminder is the scheduled delivery of an intention ([graph](../cognition/graph.md)),
not a second store of things to remember. The agenda owns nothing the graph holds:
the intention, its condition and its outcome live in the graph, and an intention has
at most one scheduled delivery at a time.

Due, claimed, started and completed are separate facts, recurrence is computed in its
requested zone, and a restart reads overdue work under ordinary admission, as
[execution](../cognition/execution.md#time) states. The agenda adds one rule: a
claim is exclusive and at-most-once, so two processes never fire one delivery.

## Delivery kinds

A schedule answers *when*; a **delivery kind** answers *what happens*. A due
delivery is a row of the schedule with a kind and a small flat payload, and the kind
selects how the proactive pass is framed and how its words are rendered.

| kind | the due delivery is |
|---|---|
| reminder | an intention's own delivery to its person or room |
| gathering | an intention Nora holds for a planned event, such as telling the group it starts |
| job result | a finished delegated job's result to the room that asked |
| relay | a message another room asked her to pass on |

- **The schedule stays one machine.** Recurrence, claiming and at-most-once do not
  change when a kind is added; a kind is data on a due delivery, not a branch of the
  scheduler.
- **One path to speak.** Every kind reaches a room through the fire path and its
  guards. A new way for her to speak is a new kind through that path, never a new
  send path beside it.
- **Kinds are dispatched by lookup.** An unregistered kind on a due delivery, or a
  payload missing what its kind requires, is a wiring fault. The delivery resolves
  to failed, is never left claimed, delivers nothing and counts as failed. It is never
  rendered as a malformed sentence.
- **Deliveries batch by kind.** Due deliveries for one person in one room on one tick
  fire as one turn only when they share a kind, so one kind's payload is never
  rendered through another's framing. Two kinds due together are two turns.
- **The payload is flat and closed.** A kind that needs structure is its own
  decision, not an automatic widening.

## A gathering

An event is an item once it happens. Before it, the plan is a claim and Nora's own
part is an intention ([graph](../cognition/graph.md)); its notification is that
intention's scheduled delivery, of kind gathering.

- The delivery names the room's real platform, so it resolves a real adapter. There is
  no placeholder platform.
- The title is copied onto the delivery when it is scheduled, because the title does
  not change after creation. A kind whose payload can change would read it live
  instead, at the cost of a new dependency.
- Her wording is her own voice, with the requester's text kept as they wrote it and
  group text escaped; a bare timestamp is never dumped.

## Invariants

- A delivery is scheduled, then claimed, then settled, and each is recorded; a
  scheduled delivery is not a sent one.
- An agenda that cannot read its schedule is unavailable, never empty.
- A watch is polled only through an intake adapter that can read one source reference
  on demand, and stops when its intention ends.
