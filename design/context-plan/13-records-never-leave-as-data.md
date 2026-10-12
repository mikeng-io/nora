---
type: design
title: "Records never leave as data — the boundary the location law should state"
description: "Operator ruling of 2026-10-12: nothing Nora holds about people is exported raw, to anyone, the subject included; she answers from it, transformed. Media that was never anyone's record (music, fetched media, what she generates or writes) is the exception. Law only, no mechanical floor. This is the real boundary node 12 §3 asked the location law to state."
tags: [nora, context-plan, doctrine, privacy, location, computer, export]
timestamp: '2026-10-12'
edges:
  - {dst: 12-the-sort-ruled.md, rel: amends, provenance: asserted}
  - {dst: 11-composition-is-the-doctrine-cut.md, rel: amends, provenance: asserted}
---

# Records never leave as data

## The ruling

Operator, 2026-10-12, after four location scenarios: in a known group, even
with a stranger or two present, "where is X?" gets an answer; a stranger asking
gets nothing (the `persons` allowlist already gives a stranger no turn); a known
member asking where X has been this morning gets an answer; "export X's full
trips from yesterday as a detailed CSV" must not happen. Then, generalising:
personal export is never allowed. No raw or original data is exported without
transformation; the exceptions are file downloads such as music.

Stated as the law:

> **Nothing Nora holds about people leaves her as data.** She answers from it —
> interpreted, in conversation, at the level a person would say it. She does not
> hand over the records themselves, in any form, to anyone, the person they are
> about included. Content that was never anyone's record — music, media she
> fetches, images she makes, documents she writes — is not covered.

It covers every store she keeps about people: location (fixes, stays, trips),
the transcript record, notes, memory cards, Standing, Character.

**The test is the form, not the range.** "Gym in the morning, then work" is an
answer. One line per stop with times, for every stop, is the records again,
whether in chat or in a file. So "where was X all last week" is answered as a
summary, never as a day-by-day itinerary; no time range needs a cap. This closes
the open question of whether a long range in chat is disclosure or extraction.

## Enforcement: law only

The operator chose law only: no mechanical floor (a turn that read raw records
cannot export a file), no new coordinate scan beyond replies. Her judgement
decides what counts as transformed. What already holds mechanically is
unchanged: the `persons` allowlist, R-31's disclosure guard, R-28's egress
block on routing arguments, and `scan_reply`'s coordinate check on replies
(`nora/agent/egress.py`).

**Known worse.** The paths stay open in code: `where_been` returns ISO times
and coordinates (DEC-156 lets coordinates flow into other tools), the computer
can write a file, `computer_export` (DEC-295) checks who may see a file, not
what is in it. A persuaded turn can still produce the CSV. This is accepted;
if a live read-back finds a raw export, a floor is the reopen.

## What this changes

- Node 12 §3's rewrite of the location law takes this text: the boundary is
  not "location is private" (false in a location-trusted room) but "answer
  from it, never hand it over". The room's opt-in stays a fact on the turn.
- `core/constraints.md` § Other people stay private is the home; it is law,
  so it is in layer A of node 11.
- `instruments/participation.md` item 2 (the gate's instruction) stops
  contradicting it once the law no longer forbids a third party's "where is
  X?" in an opted-in room.
- Both are doctrine text changes: before/after eval, and a decision-index row
  when the text lands, not before.
