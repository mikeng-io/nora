---
type: design
title: "The topology: workspace, unit, room, conversation id, and the platform beside them"
description: "Each level, what it owns and what keys it, the invariants each level holds (one replaced: unique room names instead of one room per platform), and where DMs and voice sit."
tags: [nora, topology, workspace, unit, room, conversation, platform, invariants]
timestamp: '2026-10-25'
edges:
  - {dst: ../standing-ledger/20-the-ledger.md, rel: relates_to, provenance: asserted}
---

# 10 · The topology

## 1. The levels

```
workspace        the boundary of a person's model (DEC-214)
└─ unit          a group of people: owns notes, memos, Character, unit Standing, Cognition
   └─ room       one conversation those people use: owns its transcript, room Standing, culture
      └─ conversation id   one platform thread id; a room may hold several (a moved channel)

platform adapter   transport for a network: receives, sends, reports health. Owns no state
                   about people and is never an address on its own.
```

| level | keyed on | owns | declared in |
|---|---|---|---|
| workspace | its name | the person model's boundary | `policy.toml` `[units.<u>] workspace` |
| unit | its name | notes, memos, events, Character, unit Standing, Cognition sessions | `policy.toml` `[units]`, a room's `unit` |
| room | `(platform, primary_id)` | messages, room Standing, culture, its computer scope | `policy.toml` `[[conversations]]` |
| conversation id | `(platform, id)` | nothing: it is what arrived | a room's `ids` |
| platform | its key | transport and health | `config.toml` |

## 2. What does not change

- DEC-214's hierarchy and its Honcho routing, until Honcho retires.
- A conversation id appears in at most one room (the `load_policy` guard).
- At most one **voice** room per Discord guild and one **music** channel per
  guild (`nora/policy.py`): both are per guild because Discord allows one voice
  connection per guild, not because of the unit rule.
- At most one **home room** per unit (`Policy.home_rooms`).
- R-49's gates on crossing rooms: same unit, asker allowed in both, under the
  asker's name, egress-scanned.

## 3. The invariant that replaces one-room-per-platform

**Every room of a unit has a name, and no two rooms of one unit share it.** A
room's name is its `label`, or its network when it has none
(`_room_name`, `nora/agent/tools.py`). So:

- a unit with one room per platform needs no label (each is named by its
  network, as today);
- a unit with two rooms on one platform must label at least one of them, and
  the two names must differ after case and spacing are folded (`_fold`);
- a name equal to another room's network ("Signal" as the label of a Discord
  room) is refused, because "Signal" must keep meaning the network when only
  one Signal room exists.

Startup refuses a unit that breaks it, naming both rooms, in both forms of the
file, exactly as the rule it replaces did.

## 4. Where DMs and voice sit

- **A DM is a room** whose audience is one person, with a principal. Its unit
  binding, cross-unit reads and the laundering rule belong to the private-dm
  amendment; the topology only says a DM is never a candidate for `send_to`
  unless that amendment says so.
- **A voice channel is a surface of a room, not a room** (DEC-191); unchanged.
