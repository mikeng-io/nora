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
