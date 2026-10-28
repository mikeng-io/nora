---
type: design
title: "Register — note, memo, event; and events that are derived, not reported"
description: "The three Register kinds and how they relate, the memo, the unit-wide event and its lifecycle, what cognition may do to an event on evidence nobody stated to her, each field's reasoning mode shown, the dinner case worked through, and surprises."
tags: [nora, register, note, memo, event, gathering, derivable, lifecycle]
timestamp: '2026-09-28'
edges:
  - {dst: 10-tiers, rel: depends_on, provenance: asserted}
---

# 40 · Register

## 1. The three kinds and how they meet

> 2026-10-28 (operator, ruled; [30](30-cognition.md#0-the-loop-as-ruled-2026-10-28)): the Character and Lessons adaptors named in this section retire into step ② of the one Cognition loop, which writes Character (traits and guesses; facts are rendered by code), culture and Lessons right after step ① changes an entity's relations; it has no wake rule of its own. Read "the Character adaptor" / "the Lessons adaptor" below as step ②.

| kind | settled? | closes? | example |
|---|---|---|---|
| **note** | no | yes, on its closing condition | venue still open; who has not paid |
| **memo** | yes | no; revised or withdrawn | the deposit is $500, split four ways |
| **event** | agreed or on its way | yes, when it happens or is called off | dinner Saturday 20:00 at a restaurant |

```
Record ──cites── NOTE ──links (Note.event_id) once a what+when is proposed──▶ EVENT ──reminder──▶ fire path
                   │                                                            │
                   └──closes on a settled answer──▶ MEMO ◀──leaves behind (booking, split)──┘
```

A note keeps what is still open about an event (the venue, who pays); the
event holds the dated thing. A memo can also come straight from the Record.
Reminders belong to events and to cognition's act verb, never to memos.

**The routing rule** every writer applies: settled and about the speaker
alone → `remember` (memory); settled and about the unit, someone else or
the world → memo; not settled and must not be dropped → note; dated and
attended → event; should happen at a time → a reminder.

**The same rule over the Notebook's tiers** ([05](05-data-model.md) §2).
Raw statements stay in the Record and per-person facts in Memory; the
Notebook holds what the unit's affairs, its people and Nora add up to:

| the thing | where it goes |
|---|---|
| the unit's affairs, still open | Register · note |
| the unit's affairs, settled, to keep | Register · memo |
| the unit's affairs, dated, people attend | Register · event |
| what a person, a room, the unit or Nora is like, from many instances | Character (the Character adaptor only) |
| where Nora stands toward a room, the unit or a person | Standing |
| how Nora should act here, learned from outcomes | Lessons (the Lessons adaptor only) |
| a settled fact about the speaker alone | Memory, through `remember`: not the Notebook |
| something to do at a time | a reminder: machinery, not the Notebook |

**Amended by DEC-249**: every Notebook entry is written by the subconscious, one writer per tier; the Register's is Cognition.
From the cut-over the tag path writes none of the three kinds above; a
«記低» about the unit and a date, time or RSVP are strong cues that wake
Cognition, which writes the note, memo or event ([05](05-data-model.md) §7).
The mentions below of a tag-path `memo_write` and tag-path event writes
describe the design before that ruling.

## 2. Memo

Title, body (the [12](12-schemas.md) §3 memo schema, restated whole on
revision as DEC-213 does for a note body), cited Record ids, origin room,
unit. No anchor, no closing condition, no revisit. Withdrawn
(`memo_withdraw`) only when the unit's people say it no longer holds.

Anything the room settled may be kept, a password included (operator
2026-09-28: it is in the transcript and memory already). A memo body is
exempt from `dream_screen`'s key-shape check and renders normally wherever its
lineage passes: anything said in a group is already shared with that group.
No special redaction.

Written on the tag path by `memo_write` («記低» about anything but the
speaker; about the speaker it stays `remember`) and by cognition. Read as a
`<memos>` index plus `memo_read`. The «記低» routing change is tool text under
ADR 0015, measured with `tool_selection` before and after.

## 3. Event: unit-wide, derived, with a lifecycle

**Unit-wide** (operator 2026-09-28): an event belongs to the unit, with its
origin room kept, so a plan made on Signal is the same event on Discord.
`Gathering.conversation_id` becomes the origin; a unit key is added, and
existing per-room rows describing one happening are de-duplicated across
rooms by the migration ([70](70-migration.md) §5).

**Lifecycle:**

```
staged ─▶ proposed ─▶ confirmed ─▶ happened
   │          │           └─▶ cancelled
   │          └─▶ lapsed    (the date passed with nothing agreed)
   └─▶ evicted              (it was never an event: a misread, a joke)
```

Any status before `happened` may be revised (place, time, people — same
event, history kept in an `event_revision` chain). No further statuses for
now (operator 2026-09-28).

**What cognition may do, on evidence nobody stated to her** (operator
2026-09-28): stage, propose, **revise any event's place, time and people**
whoever created it, **confirm**, **cancel**, mark **happened** or **lapsed**,
and evict its own misreads. Every change cites its evidence and keeps the
previous revision. Two guards hold ([30](30-cognition.md) §3): a revision that
moves a `told` event to `cancelled`, `evicted` or `lapsed` without `told`
evidence keeps it rendering, flagged "cancelled?", for 48 hours; and
cognition never cancels a reminder a person set, which fires with the
event's current revision, except (after E2, DEC-249) with the event it
cancels on `told` evidence.

**The newest evidence outranks the event's older revision.** A message after
the event was set, a co-location episode at another place at the event's time
([25](25-rollup.md)), a story posted from another place: each outranks the
place the event was created with.

## 4. How it was reached is shown

Every field of every revision carries its **reasoning** ([10](10-tiers.md)
§4), its citations, a confidence and `as_of`:

- `told` — a person stated it in words, to Nora or to each other; members
  agreeing among themselves counts;
- `deduced` — it follows from rows already held (the event is at C, and a
  co-location on fresh fixes puts P there, so P attended);
- `abduced` — the best explanation of evidence nobody stated: a route request,
  a story posted from a place, a co-location on stale fixes.

The `<events>` block renders each field's mode: *place: C — told 18:40;
people: 3 — deduced 20:05 from a co-location*. A value that rests only on
abduction goes out as a question ("you ended up at C, right?"), and a "yeah"
to her own guess leaves it abduced. Wrong sometimes is acceptable. Stale is
the failure this bundle exists to end.

## 5. The dinner case

An afternoon, in untagged chat: A is proposed at 14:10; at 16:30 someone
says A is full; B is suggested and the others agree; at 18:40 someone says "C
instead, closer"; at 19:05 Nora is asked for the route to C; at 20:05 presence
shows three members at C; at 21:30 three stories are posted from C.

| stimulus | session outcome |
|---|---|
| 14:10 messages (date cue) | event **proposed**: dinner today, place A, told, citing 14:10 |
| 16:30–16:50 messages | **revised** place → B, told (the members agreed among themselves), citing the messages |
| 18:40 message + 19:05 route request to C | **revised** place → C, told at 18:40; the route request cited beside it |
| 20:05 co-location episode at C (Rollup stimulus) | **confirmed**; people = the three co-located members; status and people deduced on fresh fixes (abduced if the fixes are stale), citing the episode |
| 21:30 stories + perceptions; the outing episode closes | **happened**: the `Happened` section cites the outing (interval, place, who) and the stories (what they show); a memo if anything will be asked again |

Had nobody named C and only the route request pointed there, place → C would
be abduced, and anything she said about it would go out as a question.

This case, recorded, is the bundle's first eval case: truncated at step 3 to
what exists by then (no stories, no memo), whole at steps 5 and 7
([70](70-migration.md) §2).

**Who sees it.** Lineage is per field ([10](10-tiers.md) §3). What, when and
place rest on the room's messages and render wherever that room's messages
may. Status and people place three members at C at 20:05, so they carry
`presence` and render only where the room's `location` flag is on and the
DisclosureGuard passes for each of them; elsewhere the event shows without
those two fields.

## 6. Surprises

Any Register row may name `exclude_persons`: a birthday plan is never shown to
the person it is for, in any room, by any block or tool, nor in any room with
unnamed platform accounts. A surprise cue in a cited message ("don't tell
her") makes it mandatory on the write. Code unions it into every row that
cites this one and across Register links: a note and its event
(`Note.event_id`) share it both ways, and a memo a note closes into inherits
it. It is re-checked on every link write and when a reminder about the row
fires ([10](10-tiers.md) §3).

**Not yet reachable (2026-10-03):** the session verbs carry no `exclude_persons`,
so the Cognition prelude keeps surprises out of the notebook until they do. This
deliberately differs from the above until the verb exists.
