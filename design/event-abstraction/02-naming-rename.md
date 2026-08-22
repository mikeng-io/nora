---
type: decision
title: "The Event name collision, and the Gathering rename"
description: "nora.domain.events.Event already means the gathering/RSVP feature. Resolved by renaming that module to Gathering throughout, freeing Event for the promoted Occasion concept. Tool names are unaffected."
tags: [nora, events, gatherings, rename]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
---

# The Event name collision, and the Gathering rename

## The collision

`nora/domain/events.py` already defines `Event` — the gathering/RSVP
module (title, venue, RSVPs, `EventStatus`, `RSVPResponse`, and an
`EventStore` port). It is unrelated to scheduling: an `Event` (gathering)
*owns* at most one one-shot `Reminder` via `reminder_id` and reuses the
reminders/heartbeat fire path for its own notification. It does not
compete with the promoted `Event` this bundle introduces (see
`04-dispatch-and-batching.md`) — but it does own the name.

## The rename

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

## What does not rename

The model-facing tool names (`create_event`, `update_event`,
`rsvp_event`, `cancel_event`, `list_events`, `suggest_venue`) stay as
they are. A human asking Nora to plan a dinner still says "event" — the
collision is a Python identifier collision, not a product one, and the
tool surface is a string contract with the model, not a Python name.

`nora/web/api.py`'s `GET /events`, `GET /events/{id}` routes and their
imports update to the renamed types; the URL paths themselves are
unaffected for the same reason the tool names are.

## Testing impact

Gathering's existing test suite (currently under whatever covers
`EventService`) is renamed alongside the module. Assertions that
currently check `text=f"Event: {title}"` on the reminder it schedules
move to checking `kind="gathering"` + `payload["title"]` instead — see
`05-gathering-fold-in.md`, which is the change that makes that
assertion true.
