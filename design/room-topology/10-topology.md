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
| unit | its name | notes, memos, events, Character, unit Standing, Cognition sessions, the group computer workspace (`computer_unit_scope`) | `policy.toml` `[units]`, a room's `unit` |
| room | `(platform, primary_id)` | messages, room Standing, culture | `policy.toml` `[[conversations]]` |
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

**Every room already has a label**: `load_policy` refuses a conversation
without one (`nora/policy.py`, "EVERY ALLOWED ROOM CARRIES A LABEL"), and that
stays. The new rule is added on top of it: **no two rooms of one unit share a
name, and a network's name stays the network's.** A room's name is its label
(`_room_name`, `nora/agent/tools.py`; its fallback to the network is never
reached for a policy room). So:

- two rooms of one unit whose labels are equal after case and spacing are
  folded (`_fold`) are refused;
- a label equal to a network's name is refused when that network holds two or
  more rooms of the unit ("Signal" beside "Dinner" on Signal would otherwise
  make "Signal" resolve to one room by name instead of returning `ambiguous`,
  since the resolver matches names first), and when it labels a room on a
  different network ("Signal" as the label of a Discord room);
- a label equal to its own network's name is allowed while that room is the
  only one on its network, which keeps any such label in today's file
  loading.

Startup refuses a unit that breaks it, naming both rooms, in both forms of the
file, exactly as the rule it replaces did.

## 4. Where DMs and voice sit

- **A DM is a room** whose audience is one person, with a principal. Its unit
  binding, cross-unit reads and the laundering rule belong to the private-dm
  amendment; the topology only says a DM is never a candidate for `send_to`
  unless that amendment says so.
- **A voice channel is a surface of a room, not a room** (DEC-191); unchanged.

## 5. Discord: a server entry, and where threads sit

A `[[conversations]]` entry may name a whole Discord server by `guild = "<id>"`
instead of one entry per channel. The entry itself is not a room: at runtime
every text/announcement/forum channel of the guild becomes an ordinary room of
the entry's `unit`, with conversation id `"<guild_id>/<channel_id>"` exactly as
an explicit entry's — so a per-channel entry written beside it covers the same
id and wins it. The room's label is the channel's own name, which is why the
unique-label invariant of §3 applies to it the same way: a channel name
colliding inside the unit fails closed for that channel rather than crashing
or merging, and a rename moves the label while the conversation id — and
everything bound to it — stays. `ignore`/`ambient` name channels by name:
ignored channels are never admitted; the rest are mention-only unless named
ambient, an operator opt-in like any other room's.

A Discord thread — including a forum post — is never a room and never an
entry. A message inside one belongs to its PARENT channel's room, tagged with
the thread's own channel id, which is what routes a reply back into it. A
voice channel stays a surface of a room (§4), set on a per-channel entry;
a server entry cannot carry `voice`, `music_channel`, `home_room` or
`proactive`, because each names one room's job.
