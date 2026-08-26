---
type: design
title: "Gathering-notify as a real Event kind"
description: "GatheringService stops faking a plain reminder's text field and instead schedules a proper kind=\"gathering\" reminder with a denormalized payload — the first real consumer of the registry."
tags: [nora, gatherings, events, reminders]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 02-naming-rename, rel: depends_on, provenance: asserted}
  - {dst: 04-dispatch-and-batching, rel: depends_on, provenance: asserted}
---

# Gathering-notify as a real Event kind

`GatheringService._link_reminder` (today `EventService._link_reminder`)
currently writes `text=f"Event: {event.title}"` into a plain reminder
row — a string faking the payload a real kind should carry. It becomes:

    kind="gathering"
    payload={"title": gathering.title}

`gathering_occasion(rows)` reads `payload["title"]` and builds the
header/body the way `reminder_occasion` builds the requester's sentence
today, without needing a `GatheringStore` dependency on the fire path.
The legacy `text` string is KEPT on the row, unread by the fire path
(see `03-reminder-schema.md` — `text` vs `payload` coexistence).

## The `platform="event"` sentinel — this bundle fixes it

`EventService._link_reminder` today writes `platform="event"` into the
linked reminder row (`nora/app/events.py:353`). The fire path resolves
delivery via `self._adapters[platform]` — a direct dict key whose only
wired entries are `"signal"` and `"discord"` (`nora/platform/registry.py`)
— so a gathering reminder can never be delivered today: `_adapter_for
("event")` raises `KeyError` and the notify dies as a heartbeat error,
invisible to the group. This is a LATENT bug in the existing
gathering-notify path that this bundle is the first to surface and
FIX, not a new one introduced here: a gathering reminder today is
already dead on arrival. (See the retained 2026-08-19 spec note in
`02-naming-rename.md` — that spec describes the same sentinel.)

The fix: `_link_reminder` gains a `platform: str` parameter carrying
the REAL delivery platform of the conversation the gathering belongs
to, replacing the hardcoded `"event"`. The `create_event`/
`update_event` TOOL callers pass `room.platform` (the tool layer knows
the room; `nora/agent/tools.py` — the only production caller of
`_link_reminder`). The web surface (`nora/web/api.py`) exposes only
read-only `GET /app/v1/events` list/detail routes and never writes a
gathering reminder, so it has no platform to pass — its imports update
with the rename, nothing else. The `Reminder` row then records a wired
platform, and the fired `gathering_occasion` resolves an adapter
exactly like a plain reminder does. No `_adapters["event"]` entry is
added — the sentinel is removed, not papered over.

Contract obligations added by this fix:

- A test that a `kind="gathering"` reminder row resolves a WIRED
  adapter and completes a fire turn (the old sentinel would fail it).
- A test that `platform="event"` no longer appears in any row written
  by `GatheringService` after the change.

## Why denormalize instead of a live lookup

Title is denormalized at schedule time deliberately — `Gathering.title`
has no update path in `GatheringService` today (only `when` and
`venue_text` are mutable), so a snapshot at schedule time is exactly as
current as a live read would be, at zero new coupling. The alternative
— `payload={"gathering_id": ...}` plus a `GatheringStore` dependency on
`FirePath` to read the row fresh at fire time — was considered and
rejected: it buys nothing today (title cannot have changed since
schedule time) and adds a dependency the fire path has never needed.

If a future change makes title mutable, this denormalization is the one
place that would need to become a live lookup — noted here so it is not
a silent trap later.

## Testing impact

Gathering's test suite (renamed alongside the module per
`02-naming-rename.md`) moves its assertions from `text=f"Event: ..."`
to `kind="gathering"` + `payload["title"]`. `gathering_occasion`'s own
header/body prose is not drafted in this bundle — see the open question
in `00-overview.md`.
