---
kind: spec
title: "Event abstraction — promoting Occasion, and renaming the gathering module out of its way"
description: "Reminder stays the scheduler. Occasion (today private to nora/app/fire.py) is promoted to a first-class Event concept, kind-discriminated and dispatched through a registry instead of a hardcoded call. The existing gathering/RSVP Event module is renamed Gathering to free the name, and folds in as one Event kind. Reminder gains kind+payload columns; its scheduling core is untouched."
tags: [nora, reminders, events, gatherings, heartbeat, domain, design]
timestamp: '2026-08-22'
status: proposed
---

# Event abstraction

## Why

Miles's framing, verbatim: "the reminder is the scheduler, the event is one
of the event[s] — but NOT that kinda of system event." AWS EventBridge's
split, not a generic pub-sub bus: Reminder answers *when*; something else
should answer *what happens*, as a typed, extensible thing, rather than
each new "unprompted turn" reason being a bespoke function wired by hand
into `nora/app/fire.py` and `nora/app/heartbeat.py`.

That something else already exists, informally. `nora/app/fire.py`
already has `Occasion` — `kind` + the four fields every unprompted turn
needs (`platform`/`conversation_id`/`conversation_type`/`person_id`) plus
`body`/`header` — and four factory functions that build one:
`reminder_occasion`, `compute_occasion`, `relay_occasion`,
`lost_job_occasion`. `FirePath.speak(occasion)` is already kind-agnostic.
What is missing is only that `Occasion` is private to one module, and the
one caller that turns a due `Reminder` row into an `Occasion`
(`FirePath.run`) hardcodes `reminder_occasion` rather than looking up a
factory by kind. This spec promotes what already works into a real
concept and removes that hardcoding — it is not a rewrite of the fire
path, and it is explicitly not a merge of `Reminder`'s scheduling
machinery into anything (see "Shape A vs Shape B" below).

## What this change is, and is not

**Is:** promoting `Occasion` to `Event`, a `kind`-dispatched registry
replacing one hardcoded call, two additive columns on `Reminder`
(`kind`, `payload`), and a rename of the existing gathering/RSVP module
(currently also called `Event`) to `Gathering` so the name is free.

**Is not:** a generic event bus (no pub/sub, no arbitrary system
telemetry, no subscribers), a rewrite of `reminders.py`'s scheduling
correctness (recurrence anchor math, the CAS `claim`, at-most-once,
`sweep_firing` — all untouched), or a live-lookup coupling between the
fire path and `GatheringStore` (gathering titles are immutable after
creation, so their fire-time text is denormalized at schedule time
instead — see §4).

## Shape A vs Shape B — and why this spec is Shape A

Two ways to read "Reminder becomes a branch of the Event abstraction":

- **Shape A (this spec): Reminder *produces* Event.** Two separate
  domain types. `reminders.py` gains two columns and is otherwise
  untouched. `Event` is the dispatched payload a due Reminder row turns
  into at fire time, via a `kind -> factory` lookup. Reminder is a
  producer of Events, not a case of one.
- **Shape B (rejected for this spec): Reminder *is* one kind of Event.**
  A single unified type/table; `Reminder` stops existing as a module,
  surviving only as a kind value on a generalized `Event` row that also
  carries `due_at`/recurrence/claim state directly. This is the literal
  reading of "branch of eventbus," and it buys nothing functionally that
  Shape A does not — the two converge on the same dispatch behavior —
  while touching `reminders.py`'s ~600 lines of load-bearing correctness
  reasoning (recurrence-anchor math, the claim/successor transaction,
  at-most-once) and all four of its test files, purely to change the
  type's identity rather than its behavior.

Decided: **Shape A.** Smaller blast radius, same extensibility, the
proven scheduler stays exactly as proven.

## 1. The name collision, and its resolution

`nora/domain/events.py` already defines `Event` — the gathering/RSVP
module (title, venue, RSVPs, `EventStatus`, `RSVPResponse`, and an
`EventStore` port). It is unrelated to scheduling: an `Event` (gathering)
*owns* at most one one-shot `Reminder` via `reminder_id` and reuses the
reminders/heartbeat fire path for its own notification. It does not
compete with this spec's promoted `Event` — but it does own the name.

Renamed throughout, mechanically, 1:1:

| today | renamed to |
|---|---|
| `nora.domain.events.Event` | `nora.domain.gatherings.Gathering` |
| `EventStatus` | `GatheringStatus` |
| `EventStore` | `GatheringStore` |
| `DuplicateEventError` | `DuplicateGatheringError` |
| `EventNotFoundError` | `GatheringNotFoundError` |
| `EventCancelledError` | `GatheringCancelledError` |
| `AmbiguousOpenEventsError` | `AmbiguousOpenGatheringsError` |
| `nora.app.events.EventService` | `nora.app.gatherings.GatheringService` |
| `nora.infra.sql.events` | `nora.infra.sql.gatherings` |

`RSVP`/`RSVPResponse`/`VenueCandidate`/`VenueSuggestions` are unaffected
— they never collided.

**Not renamed:** the model-facing tool names (`create_event`,
`update_event`, `rsvp_event`, `cancel_event`, `list_events`,
`suggest_venue`). A human asking Nora to plan a dinner still says
"event" — the collision is a Python identifier collision, not a product
one, and the tool surface is a string contract with the model, not a
Python name.

`nora/web/api.py`'s `GET /events`, `GET /events/{id}` routes and their
imports update to the renamed types; the URL paths themselves are
unaffected for the same reason the tool names are.

## 2. `Event`, promoted

`Occasion` moves from a private type in `nora/app/fire.py` to
`nora.domain.events.Event` (the name is now free). Its shape is
unchanged — this is a promotion, not a redesign:

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

## 3. Reminder: `kind` + `payload`

`Reminder` (`nora/domain/reminders.py`) gains two fields:

    kind: str = "reminder"
    payload: Mapping[str, str] | None = None

Defaulted so every existing row and every existing call site — the SQL
store, the in-memory fake, `remind`/`cancel_reminder`/`list_reminders`,
all four reminder test files — is byte-identical to today. `payload` is
an opaque string-keyed map; Reminder never interprets it — the same
discipline `reminders.py`'s own docstring already states about `text`
("Reminder never knows what fires"). Recurrence, `claim`, `mark`,
`cancel`, `sweep_firing`, the anchor-based occurrence math — none of it
changes; a `kind`/`payload` pair is not consulted by any of it.

Migration: two nullable/defaulted columns on the `reminders` table.
Both `ReminderStore` implementations
(`nora/infra/sql/reminders.py`, `nora/testing/reminders.py`) and
`tests/contract/test_reminder_store_contract.py` pick up round-tripping
them. No `ck_reminders_*` constraint changes — `kind`/`payload` are
independent of the existing `series`-nullability check.

## 4. Dispatch: a kind -> factory registry

`FirePath.run(rows)` today hardcodes `reminder_occasion(rows)`. It
becomes a lookup against a registry wired at the composition root:

    EventFactory = Callable[[Sequence[Reminder]], Event]
    registry: dict[str, EventFactory] = {
        "reminder": reminder_occasion,
        "gathering": gathering_occasion,   # new
    }

A new kind is a new registered function — matching the codebase's
existing idiom (`Recurrence` + `occurrence_at`, `ReminderState` +
plain functions) rather than introducing a strategy-object hierarchy for
five cases. An unregistered `kind` on a due row is a wiring bug, not a
runtime maybe: it fails the same way `_adapter_for`'s missing-platform
case already does (raise, caught by `_turn`'s outer guard, recorded as
`nora_silent_failures_total{reason="handler_error"}` — R-5, observable
rather than silently dropped).

## 5. Batching must key on kind too

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

## 6. Gathering-notify as a real kind

`GatheringService._link_reminder` (today `EventService._link_reminder`)
currently writes `text=f"Event: {event.title}"` into a plain reminder
row — a string faking the payload a real kind should carry. It becomes:

    kind="gathering"
    payload={"title": gathering.title}

`gathering_occasion(rows)` reads `payload["title"]` and builds the
header/body the way `reminder_occasion` builds the requester's sentence
today, without needing a `GatheringStore` dependency on the fire path.
Title is denormalized at schedule time deliberately — `Gathering.title`
has no update path in `GatheringService` today (only `when` and
`venue_text` are mutable), so a snapshot at schedule time is exactly as
current as a live read would be, at zero new coupling. If a future
change makes title mutable, this denormalization is the one place that
would need to become a live lookup — noted here so it is not a silent
trap later.

## 7. What is explicitly out of scope

- Any new trigger mechanism beyond time (`due_at`). Every `Event` kind
  today is still produced by a `Reminder` row reaching its due time, or
  by a non-Reminder producer calling `FirePath.speak` directly (compute
  completion, relay, lost job) — this spec does not add a fifth way for
  a turn to start unprompted, matching DEC-99/R-1b's existing rule that
  a new way to speak is a new `Occasion`/`Event` built *through*
  `FirePath`, never a new send path beside it.
- A `revisit`/note-tier reminder kind. The note-tier design
  (`docs/superpowers/specs/2026-08-21-note-tier-design.md` §9.5-9.7)
  explicitly considered and rejected this — the Dreamer only ever
  reaches someone via an ordinary reminder. Nothing here reopens that;
  a future decision to add such a kind would use exactly this
  registry, and is a separate decision.
- Renaming `nora.domain.reminders` itself, or moving Event into it.
  They stay separate modules connected by `kind` + the registry.

## 8. Testing impact

- `tests/contract/test_reminder_store_contract.py` — extend to
  round-trip `kind`/`payload` through both `ReminderStore`
  implementations (the fake must behave like the real server on the new
  columns too, per this repo's fake-fidelity rule).
- `tests/test_reminder_tools.py`, `tests/unit/test_reminder_recurrence.py`,
  `tests/infra/test_reminder_store.py` — unaffected in behavior;
  constructors picking up two new defaulted fields.
- A new `tests/test_events.py` (or renamed from wherever
  `Occasion`/`reminder_occasion` is currently covered) exercises the
  registry: an unregistered kind fails closed and observably; each
  registered kind's factory builds the expected `Event`.
- Gathering's existing test suite (currently under whatever covers
  `EventService`) is renamed alongside the module; assertions on
  `text=f"Event: ..."` become assertions on `kind="gathering"` +
  `payload["title"]`.
- `design/09-decision-index.md` gets an entry for the promotion +
  rename (DEC number assigned at merge time, per this repo's rule
  against taking one from a chat agreement).

## Open questions for review

1. Is `payload: Mapping[str, str] | None` (flat string map) sufficient
   for every kind you expect to register, or does any anticipated kind
   need structured (non-string) values? A flat map keeps the migration
   trivial (one JSON/JSONB column) but would need revisiting if a kind
   needs, say, a list or a number.
2. `gathering_occasion`'s header/body prose — not drafted here. It
   should read the way `_fire_header` reads for plain reminders: her own
   voice, escaped operator/group-authored text, no bare timestamp
   dumps. Worth a pass once this is implemented, alongside the existing
   fire-header tests.
