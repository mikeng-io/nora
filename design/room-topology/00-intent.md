---
type: design
title: "Intent: the one-room-per-platform rule was about addressing"
description: "Why DEC-105 limited a unit to one room per platform, what actually depends on it, and what changes instead."
tags: [nora, topology, unit, room, platform, dec-105, dec-214]
timestamp: '2026-10-25'
edges:
  - {dst: 10-topology, rel: sequences, provenance: asserted}
---

# 00 · Intent

## 1. The rule and its reason

DEC-105 (2026-08-14) introduced the unit so a person in Discord could ask Nora
to tell the Signal group something. Its address was the network: `send_to`
takes `platform: Literal["signal", "discord", "whatsapp", "instagram"]`, and
its docstring tells the model "the group has one room on each network, so
`platform` names a network, not a room" (`nora/agent/tools.py`). DEC-214 made
the rule a startup refusal (`_refuse_two_rooms_on_one_platform`,
`nora/policy.py`), whose own docstring gives the reason: "anything that
addresses the unit's room on a platform would otherwise be guessing".

So the rule protects an address. It is not a storage rule:

- every per-room store keys on `(platform, conversation_id)` (messages,
  Standing, the decision log; GLOSSARY «A room, and the three things it is
  not»);
- every unit store keys on the unit (notes, memos, Character, Cognition);
- the room tools already name a room by its label first and fall back to its
  network only when exactly one room of the unit is on it (`_find_room`,
  `nora/agent/tools.py`).

## 2. What the operator asked for

- A unit may hold several rooms on one platform (a general Signal group and a
  dinner Signal group), and rooms on several platforms.
- "Send it to Signal" picks the right Signal room **from context**: what is
  being passed on, what each room is for, what each room is like. When it
  cannot tell, it asks.
- Cross-room visibility is **shared state**, not a feed of each room's
  transcript into the others.
- A clean hierarchy of platform, room and unit.

## 3. What changes

1. **The address is the room.** `send_to` names a room; a network still works
   when it names exactly one room of the unit ([20](20-addressing.md)).
2. **Room names are unique within a unit**, which replaces the
   one-per-platform refusal as the invariant that keeps an address from being
   a guess ([10](10-topology.md) §3).
3. **The model chooses from what each room is for.** The room list carries
   each room's operator description, its culture's summary line, and its
   activity ([20](20-addressing.md) §2).
4. **Shared state is the unit scope of the standing ledger**
   ([standing-ledger 20](../standing-ledger/20-the-ledger.md) §6), rendered in
   every room of the unit, gated per entry. This bundle adds nothing else for
   it.

## 4. Out of scope

- DMs as rooms: the private-dm amendment.
- Whether a unit's shared state renders by default: the standing ledger's
  unit scope is gated per entry by lineage, and an off switch per unit is
  [50](50-migration.md)'s open question.
