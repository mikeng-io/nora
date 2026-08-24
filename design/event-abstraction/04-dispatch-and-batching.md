---
type: design
title: "Event promoted, dispatched by kind, and the batching fix it requires"
description: "Occasion moves from private-to-fire.py to a first-class Event. FirePath.run's hardcoded reminder_occasion call becomes a kind-to-factory registry lookup. The heartbeat's batching key must add kind to avoid rendering a mixed batch through the wrong factory."
tags: [nora, events, heartbeat, fire-path, dispatch]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 03-reminder-schema, rel: depends_on, provenance: asserted}
---

# Event promoted, dispatched by kind

## Promotion

`Occasion` moves from a private type in `nora/app/fire.py` to
`nora.domain.events.Event` (the name is free per `02-naming-rename.md`).
Its shape is unchanged — this is a promotion, not a redesign:

    kind: str                                    # "reminder" | "compute" | "relay" | "compute_lost" | "gathering" | ...
    platform: str
    conversation_id: str
    conversation_type: str
    person_id: str | None
    body: str
    header: Callable[[PersonRef | None, datetime], str]
    speaker: str = ""

The four existing kinds and their factory functions
(`reminder_occasion`, `compute_occasion`, `relay_occasion`,
`lost_job_occasion`) move with it, unchanged in behavior. Every existing
test that exercises a fire turn continues to exercise the same code
under the new name.

## Dispatch: a kind -> factory registry

`FirePath.run(rows)` today hardcodes `reminder_occasion(rows)`. It
becomes a lookup against a registry wired at the composition root:

    EventFactory = Callable[[Sequence[Reminder]], Event]
    registry: dict[str, EventFactory] = {
        "reminder": reminder_occasion,
        "gathering": gathering_occasion,   # new, see 05-gathering-fold-in.md
    }

A new kind is a new registered function — matching the codebase's
existing idiom (`Recurrence` + `occurrence_at`, `ReminderState` +
plain functions) rather than introducing a strategy-object hierarchy for
five cases. An unregistered `kind` on a due row is a wiring bug, not a
runtime maybe: it fails the same way `_adapter_for`'s missing-platform
case already does (raise, caught by `_turn`'s outer guard, recorded as
`nora_silent_failures_total{reason="handler_error"}` — R-5, observable
rather than silently dropped).

## Batching must key on kind too

`HeartbeatLoop.tick` groups due rows by `(platform, conversation_id,
requester_person_id)` and fires the batch as one turn, with
`reminder_occasion` reading `rows[0]` to name the whole batch's shape.
A mixed batch — one plain reminder and one gathering-notify, same
person, same conversation, due the same tick — would have its `kind`
decided by whichever row sorts first, and the other row's payload would
be rendered through the wrong factory.

The grouping key becomes `(platform, conversation_id,
requester_person_id, kind)`. Same conversation, same tick, two kinds:
two batches, two fire turns — no cross-kind mixing, and every existing
single-kind batch is unaffected (the fourth key component is constant
within it today, since only `"reminder"` exists).

## Testing impact

A new `tests/test_events.py` (or renamed from wherever
`Occasion`/`reminder_occasion` is currently covered) exercises the
registry: an unregistered kind fails closed and observably; each
registered kind's factory builds the expected `Event`. A heartbeat test
covers the mixed-kind batch split.
