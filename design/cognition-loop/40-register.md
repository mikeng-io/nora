---
type: design
title: "Register — note, memo, event; and events that are derived, not reported"
description: "The three Register kinds and how they relate, the memo, the unit-wide event and its lifecycle, what cognition may do to an event on evidence nobody stated, each revision's reasoning mode shown, and the dinner case worked through."
tags: [nora, register, note, memo, event, gathering, derivable, lifecycle]
timestamp: '2026-09-28'
edges:
  - {dst: 10-tiers, rel: depends_on, provenance: asserted}
---

# 40 · Register

## 1. The three kinds and how they meet

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
Reminders belong to events and to cognition's act step, never to memos.

**The routing rule** every writer applies: settled and about the speaker
alone → `remember` (memory); settled and about the unit, someone else or
the world → memo; not settled and must not be dropped → note; dated and
attended → event; should happen at a time → a reminder.

## 2. Memo

Title, body (the [12](12-schemas.md) §3 memo schema, restated whole on
revision as DEC-213 does for a note body), cited Record ids, origin room,
unit. No anchor, no closing condition, no revisit. Withdrawn
(`memo_withdraw`) only when the unit's people say it no longer holds. Anything the
room settled may be kept, a password included (operator 2026-09-28: it is in
the transcript and memory already). Written on the tag path by `memo_write`
(«記低» about anything but the speaker; about the speaker it stays
`remember`) and by cognition. Read as a `<memos>` index plus `memo_read`. The
«記低» routing change is tool text under ADR 0015, measured with
`tool_selection` before and after.

## 3. Event: unit-wide, derived, with a lifecycle

**Unit-wide** (operator 2026-09-28): an event belongs to the unit, with its
origin room kept, so a plan made on Signal is the same event on Discord.
`Gathering.conversation_id` becomes the origin; a unit key is added.

**Lifecycle:**

```
staged ─▶ proposed ─▶ confirmed ─▶ happened
   │          │           └─▶ cancelled
   │          └─▶ lapsed    (the date passed with nothing agreed)
   └─▶ evicted              (it was never an event: a misread, a joke)
```

Any status before `happened` may be revised (place, time, people — same
event, history kept).

No further statuses for now (operator 2026-09-28).

**What cognition may do, from evidence nobody stated** (operator 2026-09-28): stage,
propose, **revise any event's place, time and people** whoever created it,
**confirm**, **cancel**, mark **happened** or **lapsed**, and evict its own
misreads. Every change cites its evidence and keeps the previous revision.

**The newest human evidence outranks the event's older revision.** A message after the
event was set, a co-location episode at another place at the event's time
([25](25-rollup.md)), a story posted from another place: each outranks the
place the event was created with.

## 4. How it was reached is shown

Every revision carries its **reasoning** ([10](10-tiers.md) §4): `told` (a
person stated it to Nora, in words), `deduced` (follows from rows already
held: the event is at C and a co-location puts P there, so P attended), or
`abduced` (the best explanation of incomplete evidence: chat not addressed to
her, a route request, a story), with its citations, a confidence and
`as_of`. The `<events>` block renders it: *place: C — abduced 18:40 from chat;
deduced 20:05 from co-location*. An abduced value is said as a guess ("you
ended up at C, right?"), and a "yeah" to her own guess leaves it abduced. Wrong sometimes is acceptable. Stale is the failure
this bundle exists to end.

## 5. The dinner case

An afternoon, in untagged chat: A is proposed at 14:10; at 16:30 someone
says A is full; B is suggested and people agree; at 18:40 someone says "C
instead, closer"; at 19:05 Nora is asked for the route to C; at 20:05 presence
shows three members at C; at 21:30 three stories are posted from C.

| signal | session outcome |
|---|---|
| 14:10 messages (date cue) | event **proposed**: dinner today, A, citing 14:10 |
| 16:30–16:50 messages | event **revised** place → B, abduced, citing the messages |
| 18:40 message + 19:05 route request to C | **revised** place → C, abduced, citing both |
| 20:05 co-location episode at C (Rollup signal) | **confirmed**, place deduced, people = the members in the co-location, citing it |
| 21:30 stories + perceptions; the outing episode closes | **happened**: the `Happened` section cites the outing (time range, place, who) and the stories (what they show); a memo if anything will be asked again |

This case, recorded, is the bundle's first eval case ([70](70-migration.md)).

**Who sees it.** The confirmed row's lineage now includes `presence`, so it
renders only in rooms allowed to see members' locations ([10](10-tiers.md)
§3); elsewhere the event shows without the lines that rest on presence.

## 6. Surprises

An event or memo may name `exclude_persons`: a birthday plan is never shown to
the person it is for, in any room, by any block or tool. Set by the write
verb when the evidence says so ("don't tell her").
