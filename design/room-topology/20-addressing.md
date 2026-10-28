---
type: design
title: "Addressing: send_to names a room, the model chooses it from context, and asks when two fit"
description: "The new send_to argument and its resolution order, what the room list gives the model to choose with, the ambiguous status that makes her ask, the same-room check by identity, and why the model choosing does not move the trust boundary."
tags: [nora, send-to, relay, addressing, room-list, ambiguity, r-49]
timestamp: '2026-10-25'
edges:
  - {dst: 10-topology, rel: depends_on, provenance: asserted}
---

# 20 · Addressing

## 1. `send_to` takes a room

`send_to(room: str, text: str)`, where `room` is a room's name as `room_list`
gives it, or a network. Resolution uses the rule `room_read` already uses
(`_find_room`, `nora/agent/tools.py`), over **this unit's rooms only**:

1. a room whose name matches, case and spacing folded;
2. else a network, when exactly one room of this unit is on it;
3. else, when the word names a network with **several** rooms of this unit on
   it: `ambiguous` (§3);
4. else: the room is unknown, the same refusal for "no such name" and "another
   unit's room" (R-49's existing shape: a room of another unit is never a
   candidate, so there is nothing to tell apart).

The network fallback keeps today's phrasing working: in a unit with one Signal
room, "tell Signal" resolves as it does now.

## 2. What she chooses with

A model can only pick the right room from what it is shown. `room_list` gains,
per room, beside its name, network, connection and activity:

| field | from | why |
|---|---|---|
| `about` | the room's operator `description` (`nora/policy.py`: "`label` says what the room is CALLED; this says what it IS") | the dinner room is for dinner |
| `culture` | the first line of the room's culture account, **only when that account carries citations**: an uncited account predates the citing verb, came from a fold that also held presence reads, and is withheld from every cross-room read for that reason (`nora/language/character.py`, the `<room_cultures>` rule pinned by `tests/unit/test_character_unit.py`); the same eligibility check runs before the line is taken, and an ineligible room shows no `culture` | what the room is like, when no description says |
| `threads` | the room's newest Standing threads, by topic line | what it is currently talking about |

The `about` and `culture` fields are rendered as data about the room, never as
instructions, the rule the culture fold's `_room_block` already follows. A
turn that is asked to pass something on reads the list and calls `send_to`
with the room's name. Choosing is the model's judgement; **which rooms exist
and which are reachable stays code's**.

## 3. When two rooms fit, she asks

`send_to` with a network that names several rooms of the unit returns a new
status, `ambiguous`, with the names of those rooms and a note telling her to
ask which one. Returning the names discloses nothing: they are this unit's
rooms, which `room_list` already lists to anyone in the unit. She then asks
the person ("the general group or the dinner one?") and sends on their answer.

She also asks without calling the tool when the list itself leaves two
candidates plausible. The tool's `ambiguous` is the backstop for the case
where she did not notice.

## 4. Same room, by identity

Today the relay refuses when the destination's platform equals the source's
(`same_room`, `nora/agent/relay.py`). With two Signal rooms that refuses a
legitimate send from the general group to the dinner group. The check becomes
**the same room** (the same `(platform, primary_id)`), not the same network.

## 5. The trust boundary does not move

R-49's gates run unchanged, in their order: an unmapped asker gets nothing;
both rooms must share a unit; the asker must be allowed in the destination;
the text passes the egress scan. The model choosing among the unit's rooms
changes **which allowed room** a message goes to, never **whether** a room is
allowed. A wrong pick lands in a room of the same people, under the asker's
name, and her reply names the room it went to ("sent to the dinner group"), so
the asker sees a wrong pick at once.

## 6. The docstring

The former one said "the group has one room on each network". It is
rewritten to ADR 0015 for the new argument: name the room as `room_list` gives
it, or the network when the group has one room there; ask when two fit. The
rewrite is measured with `tool_selection` before and after
([40](40-verification.md) §2).
