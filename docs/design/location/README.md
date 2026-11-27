---
class: design
subject: location, reading where people have been and grounding journeys, places and live conditions
owns: [location reads, place resolution, wayfinding, live structured data]
area: location
links:
  - {to: docs/design/cognition/boundaries.md, rel: uses}
  - {to: docs/design/cognition/retrieval.md, rel: uses}
  - {to: docs/design/presence/README.md, rel: uses}
---

# Location

Location answers questions about places and movement: where someone is or has been,
how to get somewhere, what a place is, and what the conditions are now. It reads; it
holds no store of its own. People's positions come from [presence](../presence/README.md),
and what she may say about a place or a person is set by
[boundaries](../cognition/boundaries.md).

## Parts

| part | answers |
|---|---|
| **position reads** | where a person is now, and where they have been: a day, the places, a rhythm, the routes |
| **place resolution** | which saved or public place a name means |
| **routing and geocoding** | how to travel between places, including with a future arrival |
| **live structured data** | what is true now: transit arrivals, parking and transport state, weather |
| **wayfinding** | composing schedules, live readings, routes and the person's own intent into one answer |

## Position reads

- **They read computed episodes, never fixes** ([retrieval](../cognition/retrieval.md)).
  A day, the places visited, a rhythm and routes are derived from stays and trips;
  a forecast of where someone will be is derived from their routine and is labelled
  a forecast, never a reading.
- **Every answer carries its age.** A live position is as old as its fix, and a stale
  one reads as stale. Derived history is never presented as right now.
- **The audience is the room's current roster.** A read is made for the room that
  asked and is revalidated against the roster immediately before the result is
  released, including after dependent reads. Names resolve in that room's scope.
- **Places are named, never coordinates.** What a room may hear, and what may leave
  Nora, follow [boundaries](../cognition/boundaries.md#egress); location adds only
  that answers say where in words.
- An unreadable source is unavailable, never "not there". A person not sharing is a
  known state, not an error and not nothing.

## Place resolution

A household place is a named landmark, not a person's position. A name resolves
against operator and saved places first, within the room's permitted circle, and only
then against a public lookup. A name that is unknown or outside the circle falls
through to the public lookup without revealing that a guarded place exists.

A saved place's coordinate may stand in for a route the person asked for, inside code.
The model sees only the place's name.

## Sources compose

Sources that answer different questions compose; they never fail over into a
semantically different one.

- **Live structured data** answers the present: arrivals, vacancies, weather. It has a
  short useful life and its own timestamp. Free text inside a structured envelope is
  untrusted input and never an instruction.
- **Routing and geocoding** answers how to travel, including a future arrival. A
  route is not a live arrival prediction, and a live prediction cannot answer a future
  journey.
- A schedule plus a live arrival can guide a departure; a saved place plus a route can
  guide a journey; a person's own answer about intent settles what no source knows.
- A forecast is distinguished from a direct reading, and a clipped page, a stale
  vacancy or a partial route is never presented as a complete current answer.

## What "nothing found" means

An operator that returns no rows is nothing found only when the question was actually
asked and that source is authoritative for it. The wrong operator, missing
configuration, an upstream error and an inability to check are all unavailable, and
several operators are queried or deliberately resolved so the first empty answer does
not become proof of absence. Time and coverage travel with every result.
