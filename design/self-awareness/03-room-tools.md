# 03 — Room tools: three questions, three disclosure tiers

Realizes the **Where** layer of `01-three-layers.md` for the facts needed only
sometimes. `<presence>` (`02`) answers what every turn needs; these answer
what someone asks for.

## Why three tools, not one

Each answers a different question, returns a different shape, and discloses a
different amount. One tool with a `mode` argument would have to carry the
strictest boundary for all three, or the loosest. Neither is right.

| Tool | Question | Returns | Discloses |
|---|---|---|---|
| `my_rooms` | which rooms am I in | network, label, unit relation, member count, transport state | that a room **exists** |
| `room_activity` | which rooms are active, and since when | per room: last message time, message count over a window. No text, no names | that a room is **busy** |
| `read_room(room)` | what is being said over there | recent messages, same shape as `load_transcript` | **what people said** |

All three follow R-27: `ok`, `nothing_found`, `unavailable`, never an error
presented as an empty answer. All are `read_only=True`. None return room ids
(R-18). `read_room` takes a room by **label as `my_rooms` returned it**, and
the tool resolves it; an unknown label is `nothing_found`, not a guess.

`read_room` output is chat other people wrote, and gets the same untrusted
wrapping (R-13) and the same "never an instruction to you" framing that
`load_transcript` carries.

## D1: the unit boundary (decided 2026-09-23: B)

Units (DEC-105) exist so one group's talk does not reach another. These tools
are the first thing able to cross that line from inside a turn, so the
boundary has to be set before any of them is built.

| Option | `<presence>` / `my_rooms` shows | `room_activity` | `read_room` |
|---|---|---|---|
| **A. Same unit only** | this unit's rooms; nothing else | this unit | this unit |
| **B. Existence across units** (recommended) | this unit's rooms by name; "you are also in other groups", no names | this unit | this unit |
| **C. Names across units** | every room by name | every room | this unit only |
| **D. Open** | every room | every room | any room |

**Decided: B** (operator, 2026-09-23). Nora saying she is only in this group would be false,
and a false answer about herself is the failure `00` opens with. Naming the
other groups tells this room about people who never agreed to be mentioned in
it. B is the only option where every sentence is both true and discloses
nothing a member of this room could not already guess. `read_room` never
crosses a unit under any option short of D, and D is listed for completeness,
not recommended.

**Deferred, not decided:** the operator's own DMs, or an operator-only room,
may want option D. If it is ever wanted it is a per-room flag in
`policy.toml`, not a global setting, and it defaults off. v1 ships B
everywhere.

## What already exists and is reused

- `group_members` stays as it is: members of *this* room. It is not folded into
  `my_rooms`, because "who is here" and "where am I" are different questions
  that already route correctly.
- `send_to` keeps naming a **network**. Once `<presence>` lists the unit's
  rooms, it no longer needs its "work out which of the four networks they mean"
  paragraph to carry the room model on its own.
- `room_activity`'s counts come from the stored messages Nora already keeps
  for transcripts; no new capture.
