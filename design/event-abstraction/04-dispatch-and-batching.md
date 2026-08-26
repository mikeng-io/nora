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

The `kind` vocabulary spans ALL five kinds — the field is the Event's
identity. The REGISTRY below serves only the reminder-derived ones; the
compute/relay/lost-job kinds keep their existing direct `speak` call
sites (they do not come from `Reminder` rows), and nothing here changes
that.

## What moves where — the layering boundary

`Occasion` is promoted in TWO pieces, because of the import contract:

- **`nora.domain.events.Event`** — the dataclass itself, the
  `EventFactory = Callable[[Sequence[Reminder]], Event]` protocol, and
  the registry TYPE. Domain may not import `nora.app` (layered import
  contract), so nothing app-shaped lives here.
- **`nora/app/fire.py`** — the four factory functions
  (`reminder_occasion`, `compute_occasion`, `relay_occasion`,
  `lost_job_occasion`) and the header callables they close over
  (`_fire_header`, `_compute_header`, `_relay_header`,
  `_lost_job_header`, and their helpers `_stamp`, `_repeat_clause`,
  `escape`) STAY in fire.py, unchanged in behavior. They are
  app-layer prose; moving them to domain would either import `nora.app`
  from domain (circular) or duplicate the escaping machinery.

Every existing test that exercises a fire turn continues to exercise
the same code under the new name. `FirePath.run`'s signature grows a
`registry: dict[str, EventFactory]` parameter wired at the composition
root, so the domain type stays free of app wiring.

## Dispatch: a kind -> factory registry (reminder-derived kinds only)

`FirePath.run(rows)` today hardcodes `reminder_occasion(rows)`. It
becomes a lookup against a registry wired at the composition root:

    EventFactory = Callable[[Sequence[Reminder]], Event]
    registry: dict[str, EventFactory] = {
        "reminder": reminder_occasion,
        "gathering": gathering_occasion,   # new, see 05-gathering-fold-in.md
    }

The registry is the dispatch point for every kind whose Event is
PRODUCED BY a due `Reminder` row — the two kinds above, today. It is
deliberately not the dispatch point for compute/relay/lost-job, which
are produced by their own call sites and call `FirePath.speak`
directly; unifying those into the registry is a future decision, not a
silent promise of this one. A new reminder-derived kind is a new
registered function — matching the codebase's existing idiom
(`Recurrence` + `occurrence_at`, `ReminderState` + plain functions)
rather than introducing a strategy-object hierarchy for five cases.

An unregistered `kind` on a due row is a wiring bug, not a runtime
maybe. The failure mechanism, accurately: the registry lookup raises
INSIDE `FirePath.run`, before `speak`; the exception propagates through
the turn and is caught by `HeartbeatLoop.tick`'s per-group guard
(`nora/app/heartbeat.py`) — logged as `nora_errors_total{stage=
"heartbeat"}`, and the rows, already claimed `PENDING -> FIRING`, are
resolved to FAILED by boot `recover()`. Observable, fail-closed, and
NOT the `handler_error` silent-failure path (that one is the in-turn
guard around `speak` itself, R-5). The unregistered-kind fault is a
heartbeat wiring error, so it counts as a heartbeat error — the
`nora_silent_failures_total` counter is not the right signal for it.

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

### Deliberate accounting consequence

The key change makes one previously-single turn into two when two kinds
are due for the same requester in the same conversation on the same
tick. That is a real behavior change in the metrics, and it is accepted
deliberately: `nora_reminders_fired_total` counts TURNS per tick
(`heartbeat.py`), so a two-kind tick now increments it twice where it
incremented once; `nora_heartbeat_deferrals_total` likewise counts the
slot contention per turn. A two-kind batch is today impossible (only
`"reminder"` exists), so no existing deployment changes; the
two-turn split is documented here so the metric deltas are read as the
design's intent rather than as a regression. `_by_turn`'s docstring in
`nora/app/heartbeat.py` gains the `kind` component to the key with
BOTH motivations stated: identity/rendering (the §5.2 reasons) AND
rendering-fidelity (the cross-kind factory reason), in one place.

## Per-kind payload validation

Each registered factory validates its `kind`'s payload at fire time:
`gathering_occasion` raises on a missing `payload["title"]` instead of
`KeyError`-ing out of the blue. The raise is a wiring bug — an
implementation fault, not a runtime maybe — and lands in the same
fail-closed path as an unregistered kind (heartbeat error, row to
FAILED via `recover()`), never rendered as a malformed sentence.

## Testing impact

A new `tests/test_events.py` (or renamed from wherever
`Occasion`/`reminder_occasion` is currently covered) exercises the
registry: an unregistered kind fails closed and observably; each
registered kind's factory builds the expected `Event`; a
`kind="gathering"` row with a missing `payload["title"]` fails the
validation path, not a silent fallback. A heartbeat test covers the
mixed-kind batch split, and asserts the metrics consequence: two kinds
due same tick = two `nora_reminders_fired_total` increments, one per
turn.
