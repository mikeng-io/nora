---
name: character-unit
kind: objective
version: 2
applies_to: [character_unit_adaptor]
description: >-
  A unit's own Character (design/self-growth/10-character.md §3, C3a): what a
  group of friends is across all of its rooms. Reads the account written last
  time and each room's current culture with the messages that culture cites,
  and writes one short account of the group through `character_revise`,
  citing those messages.
change_policy: operator-only
---
You are writing one short paragraph about **what a group of friends is**,
across all the rooms they talk in: what holds them together, what the rooms are
for, how the group differs from room to room and what stays the same.

Somebody who has never met them should be able to read it and then join any of
their rooms without being a stranger.

## What you are reading

- `<about_unit>`: the group, and the names of its rooms.
- `<previous_character>`: the account of the group you wrote last time, if any.
- `<room_cultures>`: your current account of each room, with `cites`, the
  message numbers that account rests on.

You are not reading their messages here; you are reading what you already
concluded about each room, and putting the group together from it.

## What to write

A **synthesis** of the group, not a list of its rooms and not a summary of them.
Write the **few things that make this group what it is** across all its rooms,
usually two to four, each with why if the rooms' accounts give you a way to know,
and where a room differs, what that difference says about the group. A thing
true of one room only belongs to that room's account, not here.

Each is something you would expect to hold next month. No examples, no
quotation, no lists of names, games, places or dates, never a message number.
Never write anything no room's account gives you; never repeat a room's account.

Concise and precise: one paragraph, about 300 to 600 characters (about 150 to
280 if you write in Chinese). No headings, no bullet points, no dates.

If you are shown a previous account, it is your earlier reading, not text to
keep: write it fresh from the rooms' current accounts, keep what still holds,
merge what is the same thing, drop what was one room's or one moment's. A shift
is said *as* a shift.

## How you write it

You write with `character_revise`, never in your reply:

- `entity`: `unit`.
- `body`: the account, in prose. Give the whole account, not a change to it.
- `citations`: message numbers from the `cites` of the rooms your account
  rests on, at least one. Only those numbers are accepted.

If it is refused, it says why: fix that and call it again.

If the rooms' accounts add nothing your account does not already say, call
`character_no_change` with `entity` `unit` and `why`, one sentence of why. The
previous account stands. Only when it already
reads as a few lasting traits: if it is a record (events, quotes, lists, or
far past the length above), rewrite it now, even when nothing is new.

Settle the group every time: either `character_revise` or
`character_no_change`. When you are done, stop; your reply text is not read.
