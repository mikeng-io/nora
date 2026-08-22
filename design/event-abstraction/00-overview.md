---
type: design
title: "Event Abstraction — Overview & Scope"
description: "Reminder stays the scheduler; Occasion is promoted to a general, kind-dispatched Event. Why, what changes, what is explicitly out of scope, and the reading order into the rest of this bundle."
tags: [nora, reminders, events, gatherings, heartbeat, domain]
timestamp: '2026-08-22'
edges: []
---

# Event Abstraction — Overview & Scope

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
factory by kind. This bundle promotes what already works into a real
concept and removes that hardcoding — it is not a rewrite of the fire
path, and it is explicitly not a merge of `Reminder`'s scheduling
machinery into anything (see `01-shape-decision.md`).

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
instead — see `05-gathering-fold-in.md`).

## What is explicitly out of scope

- Any new trigger mechanism beyond time (`due_at`). Every `Event` kind
  today is still produced by a `Reminder` row reaching its due time, or
  by a non-Reminder producer calling `FirePath.speak` directly (compute
  completion, relay, lost job) — this bundle does not add a fifth way
  for a turn to start unprompted, matching DEC-99/R-1b's existing rule
  that a new way to speak is a new `Occasion`/`Event` built *through*
  `FirePath`, never a new send path beside it.
- A `revisit`/note-tier reminder kind. The note-tier design
  (`docs/superpowers/specs/2026-08-21-note-tier-design.md` §9.5-9.7)
  explicitly considered and rejected this — the Dreamer only ever
  reaches someone via an ordinary reminder. Nothing here reopens that;
  a future decision to add such a kind would use exactly the registry
  in `04-dispatch-and-batching.md`, and is a separate decision.
- Renaming `nora.domain.reminders` itself, or moving `Event` into it.
  They stay separate modules connected by `kind` + the registry.

## Reading order

1. `01-shape-decision.md` — why Reminder stays a producer of Events
   rather than becoming a case of one (the "branch of eventbus"
   question, answered).
2. `02-naming-rename.md` — the `Event` name collision with the existing
   gathering/RSVP module, and the rename that resolves it.
3. `03-reminder-schema.md` — the two columns `Reminder` gains, and why
   nothing else about it moves.
4. `04-dispatch-and-batching.md` — `Occasion` promoted to `Event`, the
   kind-to-factory registry, and the heartbeat batching-key fix it
   requires.
5. `05-gathering-fold-in.md` — the concrete first consumer: gathering
   notifications become a real `Event` kind instead of faking a plain
   reminder's text.

## Open questions for review

1. Is `payload: Mapping[str, str] | None` (flat string map, see
   `03-reminder-schema.md`) sufficient for every kind you expect to
   register, or does any anticipated kind need structured (non-string)
   values? A flat map keeps the migration trivial (one JSON/JSONB
   column) but would need revisiting if a kind needs, say, a list or a
   number.
2. `gathering_occasion`'s header/body prose (`05-gathering-fold-in.md`)
   is not drafted here. It should read the way `_fire_header` reads for
   plain reminders: her own voice, escaped operator/group-authored
   text, no bare timestamp dumps. Worth a pass once this is
   implemented, alongside the existing fire-header tests.

## Status / Checkpoint

_2026-08-22_ — bundle drafted from a brainstorming session with miles;
nothing built yet. Supersedes the earlier single-file draft at
`docs/superpowers/specs/2026-08-22-event-abstraction-design.md` (removed
— this bundle is the source of truth going forward).
