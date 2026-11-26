---
type: decision
title: "Shape A vs Shape B — Reminder produces Event, not a case of it"
description: "Two ways to read 'Reminder becomes a branch of Event.' Decided: Reminder stays its own module and produces Event instances via a registry (Shape A), rather than being merged into a single unified Event/EventBus type and table (Shape B)."
tags: [nora, reminders, events, decision]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
---

# Shape A vs Shape B

## The question

Miles asked directly: is this "consolidating reminder + event into an
eventbus-like of things, [with] reminder stay[ing] as a branch from
eventbus"? That is a real fork, not just naming.

## Shape A: Reminder *produces* Event

Two separate domain types stay separate. `reminders.py` — the whole
scheduling machinery (recurrence math, the CAS `claim`, at-most-once,
`sweep_firing`) — is untouched except for two added columns (`kind`,
`payload`; see `03-reminder-schema.md`). `Event` is the dispatched
payload a due Reminder row turns into at fire time, via a kind-to-factory
lookup (`04-dispatch-and-batching.md`). Reminder is a producer of Events,
not a case of one. Small diff: two additive columns, one new registry,
no schema/table merge.

## Shape B: Reminder *is* one kind of Event

One unified type/table — call it `Event` — and what is currently
`reminders.py`'s `Reminder` stops existing as its own thing; it becomes
"an Event whose trigger is time-based" (`due_at`, recurrence, claim state
all just fields on the general Event row). "Reminder" survives only as a
kind value, not a module. This is the literal reading of "branch of
eventbus" — but it means renaming/merging the single most heavily
fortified, correctness-critical file in this codebase (the
recurrence-anchor math, the claim/successor transaction, at-most-once —
`reminders.py`'s ~600 lines of load-bearing "why", plus all four of its
test files and both store implementations) purely to change the type's
identity, not its behavior.

## Decided: Shape A

Functionally, A and B end up able to do the same thing — pluggable
kinds, one dispatch point, extensible. B just makes that literal in the
type/table name; A gets there by composition (`kind` + `payload` +
registry) while leaving the proven scheduler alone. Same extensibility,
far smaller blast radius, doesn't touch the machinery with the most
invariants riding on it. If a future need makes the *identity* — one
visible type/table, Reminder obviously subordinate to it — actually
matter (not just naming), that is Shape B, revisited as its own
decision, not folded into this one.
