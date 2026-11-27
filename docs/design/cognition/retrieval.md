---
class: design
subject: "one read semantics for prefetch and read tools"
owns: []
area: cognition
links:
  - {to: docs/design/cognition/README.md, rel: uses}
---

# Retrieval

Retrieval is the one read semantics behind prefetch and every read tool: scope
first, resolve what is asked once, find seeds, walk edges, load current rows.

## Steps

| step | actor | does |
|---|---|---|
| **scope** | code | fixes the unit, room, identities, exclusions and exposure the read may see, before any search |
| **resolve** | model | turns "that image", "we", "back then" into a reference with handles, time constraints and what stays ambiguous |
| **seed** | code | exact references first, then keyword and vector search inside the scope |
| **walk** | code | follows edges from the seeds, bounded in depth, fan-out, rows and time |
| **load** | code | reads each row's current revision from its table |
| **return** | code | a subgraph: rows with kind, identity, time, lifecycle state, basis and evidence handles |

Prefetch is retrieval with code-chosen anchors; a read tool is retrieval with
model-chosen anchors. Both return the same shape.

## Prefetch anchors

| pass | anchors |
|---|---|
| called | the addressing item, its room, its people |
| ambient | the room's recent window and its people |
| proactive | the due intention, what it rests on and serves, its room and people |
| wake | the claimed stimuli's items, rooms, people and the intentions they touch |
| debrief | the pass's own items, its room, and the open intentions for the room and unit |
| review | the intentions closing or past due, and the claims and lessons due for grading |

## Rules

- A boundary is applied before the search; filtering after a global search is
  not a boundary.
- A search score orders candidates. It is not the probability that something
  is true, and never outranks a row's current revision.
- A claim is returned with any items about the same subject recorded after it
  was asserted, so a fresh direct statement beats stale understanding before
  reflection has revised it.
- Raw location fixes are never indexed; only computed episodes such as stays
  and trips are a read surface.
- Structural edges support evidence lookup; an identity is never a fan-out to
  everything they wrote.
- Current content comes from the row's table, so stale index text cannot
  revive a retired claim.
- Hitting a bound returns partial; a failed read returns unavailable, never
  nothing found.
- A read marks rows the pass was already shown and states how many were new;
  repeats are metadata, never a status.
- A returned row explains itself: when it occurred and was asserted, who it
  concerns and how (author, depicted, participant), its basis, and what would
  close or end it.
