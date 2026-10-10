---
type: design
title: "Target shape: context awareness is one View of what is true now, not a block per producer"
description: "The operator expects both the conscious and the subconscious to be aware of the people's world now: where they are, what they are doing, what is coming, not only Nora's own state. An inventory read from origin/master on 2026-10-10 finds that, beyond the clock and the room's own memory, every such dimension is a tool she must think to call, and that Cognition is the only reader any of it is pushed to. This node records the inventory, names the cause, and sets the target shape inside DEC-249's data model: the situation is a View slice over the Record, the Rollup and open Notebook entries, Nora is one entity in it, and `<presence>` becomes her row of it rather than a block of its own. Nothing is built or ruled here."
tags: [nora, context, awareness, presence, location, views, data-model, measurement]
timestamp: 2026-10-10
status: proposed
edges:
  - {dst: 00-intent.md, rel: follows, provenance: asserted}
  - {dst: ../cognition-loop/05-data-model.md, rel: depends_on, provenance: asserted}
  - {dst: ../context-assembly/04-every-occasion.md, rel: extends, provenance: asserted}
---

# Target shape: context awareness

## 0. What the operator asked for

Operator, 2026-10-10: Nora in both the conscious and the subconscious is not
context-aware, starting with location: the sender's and the members' real-time
whereabouts. Both should carry "context awareness": location awareness and
every other awareness of the same kind. He also named the constraint on the
answer: some context blocks already conflict with each other, so the answer is
not another block, and he intends to retire Honcho and give Nora one memory
system of her own instead of profiles and state scattered across producers.
That memory system is not being built now; this node sets the shape the
situation takes inside it.

## 1. What each reader is given today

Read from origin/master at `f052291fd` on 2026-10-10 (a read-only inventory,
file and line references in each cell's source). Apart from the clock and the
transcript ages, every source below sits behind a flag that ships off; the live
`config.toml` on the deploy host was **not** read, so "pushed" means "pushed
where that flag is on".

| Awareness | Conscious (tag, fire) | Subconscious (Cognition) |
|---|---|---|
| where someone is now: place, moving or driving, fix age | pull only: `where_is` (`nora/agent/whereis.py`), refused unless the room is `location = true` or its people are a subset of the circle (R-31) | no position: `where_is` is not in Cognition's catalogue (`nora/app/cognition_wiring.py`); the one "now" fact pushed is an open co-location, who is together with no place |
| usual places, routine, anomalies | pull: `where_been`, `routine_read`, `rollup_read` | pushed: deviation and co-location rollups as stimuli (`nora/analytics/units.py`, `nora/app/cognition_grounding.py`) |
| the clock, each message's age | pushed: the clock line, `age` on every transcript row | pushed: the clock and "since she last looked" |
| time since a person last spoke | absent as a fact | absent |
| reminders | pushed only as the occasion of a due reminder's fire turn | not shown, though Cognition may write them |
| events | pull: `event_search` | pushed: the events block and the `upcoming` stimulus; the arrival-at-event-place stimulus is inert, its only implementation is `NoEventPlaces` |
| weather | pull: `weather` | absent |
| online, in voice, playing | pull: `online_now`, `music_status` | absent: the `play` and `social` stimulus kinds are declared with no producer (`nora/domain/stimuli.py`) |
| holidays, mood | no data source | no data source (inferring moods is refused by the objectives) |
| who the people are, the room's state | pushed: room_members, peer_card, peer_history, character, culture, Standing, notes, memos, transcript | partly pushed, the rest pull |
| Nora's own transports | pushed: `<presence>` (`nora/agent/prefill.py`, `nora/language/presence_render.py`) | room labels only |

## 2. The cause

**Awareness was built as tools.** A tool answers a question she has already
thought to ask; awareness is the situation already being in what she reads.
The conscious is given her own state (`<presence>`) and the room's memory, and
the people's world only on request. The measured cost of optional calls is on
record: an optional act left to the model is almost never chosen.

**The blocks that are there are fragments of the same few questions,** each a
different producer's copy, with nothing telling her which one wins:

- *what is true now*: the clock line, `<presence>`, `<people_in_window>`, and
  the situation block this bundle would otherwise add;
- *who someone is*: `<room_members>`, `<peer_card>` (Honcho), `<character>`,
  `<peer_history>`;
- *what is open or planned*: `<standing>`, `<standing_working_set>`, `<notes>`,
  `<memos>`, `<loop_decisions>`. One collision here is already measured:
  Standing folds dropped dated plans the notes own ([10](10-standing-drops-plans.md)).

The other two groups are read off the block list, **not measured** as
conflicts.

## 3. The target shape

DEC-249 ([05](../cognition-loop/05-data-model.md) §1, §6) already holds the
answer's structure: Record → Rollup → Notebook → Views, one assembly path, a
View cut per reader at read time and never stored. Under it, **a situation is
not a block a producer writes, it is a View slice**: for every entity in the
reader's scope, what is true now and what is open, as of the turn, under the
room's lineage and disclosure.

**Nora is an entity in it like everyone else.** `<presence>` and a members'
situation differ only in whose rows they are: her transports' health and the
platform she is on are her Record rows (a new placement: 05 §1a does not
place them, and today they are live adapter state read at turn start); a
member's latest fix, whether they
are moving, online or in voice, and their next reminder or event are theirs.
`<presence>` stops being a block and becomes her row of the slice.

The slice answers four questions, each with its own time scale and its own
sources. They are facets of the View: not Notebook tiers, and not a new kind of
row at any layer.

| Facet | Changes | Sources | Scattered across today |
|---|---|---|---|
| **identity**: who someone is | slowly | Authored (identity, policy), Notebook (Character), Memory (beside the flow) | room_members, peer_card, character, Honcho |
| **state**: what is true now, and it ages | by the minute | Record (latest fix, adapter health, online, play), Rollup (current stay) | `<presence>`, the clock line, `where_is`, `online_now`, `music_status` |
| **commitments**: what is open until it closes | until closed | Notebook (notes, events, Standing); reminders, which are machinery, not a layer | notes, events, reminders, Standing |
| **episodes**: what happened and what was settled | appended | Record (transcript, her decisions), Notebook (memos, culture) | transcript, memos, loop_decisions, culture |

Rules the slice inherits, not new ones: every row carries its age (a state row
without an age is not rendered, because the judgement it serves turns on
*when*); a person's state rows pass the same disclosure test `where_is`
applies (`presence_audience`, the room's `location` flag), once, at render,
not in each producer; an absent source renders as absent, never as silence.

**The subconscious gets the same slice and the change events that move it.**
Cognition reads the slice for every room on its agenda, and is woken by the
state facet changing: an arrival or departure, a co-location starting, a
member nearing an event's place (wiring a real `EventPlaces`), and producers
for the declared `play` and `social` kinds. This is the ambient consumer
issue #9 tracks, now Cognition's.

**Memory.** Retiring Honcho moves the identity facet's last outside source
inside the flow and amends DEC-249's "Memory beside the flow". That is the
operator's stated direction, not a ruling; it needs its own decision.

## 4. What may happen before the memory system

Not another block. If anything ships first, it is one `<now>` row in
`prefill._BLOCKS` that **replaces** `<presence>` and the clock line and carries
the state facet for Nora and the people in the turn, so the count of blocks
goes down and the shape is already the View's. Whether `<people_in_window>`
(participation turns only) folds into it is open.

Before building it, measure it: replay recorded turns where where-or-when
decides the reply, with and without the state facet, on the Langfuse replay
harness. The usual-places block measured null on 2026-09-25 (00 §4.1), so a
positive effect is not assumed. First, read which sources are on in the live
`config.toml`, since a slice over sources that are off renders nothing.

## 5. Open

- Whether `<now>` ships before the memory system or waits for it.
- Which time scale the state facet's age is shown in (relative, absolute, both).
- Whether battery (recorded, read by no model) belongs in the state facet.
- The decision that retires Honcho and amends DEC-249's Memory row.

## Status / Checkpoint

2026-10-10: proposed. Inventory read from origin/master `f052291fd`; the live
config not read. Nothing built, nothing ruled.
