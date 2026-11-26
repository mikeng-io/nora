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
| `nora.web.events` | `nora.web.gatherings` |
| `nora.testing.events` | `nora.testing.gatherings` |
| `normalise_event` | `normalise_gathering` |

`normalise_event` (`nora/domain/events.py:178`) is a public port-level
function imported by both store implementations
(`nora/infra/sql/events.py`, `nora/testing/events.py`) — it is a
collision site like any other and renames with the module.

Test files that exercise the module rename alongside it, 1:1:

| today | renamed to |
|---|---|
| `tests/test_events_domain.py` | `tests/test_gatherings_domain.py` |
| `tests/test_events_service.py` | `tests/test_gatherings_service.py` |
| `tests/test_event_tools.py` | `tests/test_gathering_tools.py` |
| `tests/test_web_events.py` | `tests/test_web_gatherings.py` |
| `tests/contract/test_event_store_contract.py` | `tests/contract/test_gathering_store_contract.py` |
| `tests/web/test_api.py` | (kept — covers the `/events` routes; its `GatheringService`/`RSVPResponse`/`FakeGatheringStore` imports update to the renamed types) |

`RSVP`/`RSVPResponse`/`VenueCandidate`/`VenueSuggestions` are unaffected
— they never collided.

## The retained 2026-08-19 spec

`docs/superpowers/specs/2026-08-19-event-module-design.md` is still live
and describes the feature under the old vocabulary — `Event`,
`platform="event"`, `text=f"Event: …"`. It is superseded for its
naming but not for its behaviour; rather than deleting it (its
behavioural prose remains the Gathering feature's contract), it gains a
header note pointing at this bundle as the rename + `kind="gathering"`
authority, and its identifier references are updated where they name
renamed types. The plan doc that references it is updated the same way.
The single-file draft at
`docs/superpowers/specs/2026-08-22-event-abstraction-design.md` remains
removed — this bundle is the source of truth.

## What does not rename

The model-facing tool names (`create_event`, `update_event`,
`rsvp_event`, `cancel_event`, `list_events`, `suggest_venue`) stay as
they are. A human asking Nora to plan a dinner still says "event" — the
collision is a Python identifier collision, not a product one, and the
tool surface is a string contract with the model, not a Python name.

`nora/web/api.py`'s `GET /app/v1/events`, `GET /app/v1/events/{id}`
routes and their imports update to the renamed types; the URL paths
themselves are unaffected for the same reason the tool names are.

## Testing impact

Gathering's existing test suite (currently under whatever covers
`EventService`) is renamed alongside the module. Assertions that
currently check `text=f"Event: {title}"` on the reminder it schedules
move to checking `kind="gathering"` + `payload["title"]` instead — see
`05-gathering-fold-in.md`, which is the change that makes that
assertion true.
