# 03 — Room tools: two tools, split where content starts

Realizes the **Where** layer of `01-three-layers.md` for the facts needed only
sometimes. `<presence>` (`02`) answers what every turn needs; these answer
what someone asks for.

## Two tools, not three (revised 2026-09-24)

The first version had three tools, one per disclosure tier: existence
(`my_rooms`), busyness (`room_activity`), content (`read_room`). Two things
changed it:

- **ADR 0015** (PR #420, tool-catalogue session) makes every new tool
  `<domain>_<verb>`, with the verb from a closed list enforced by that PR's
  tool-name architecture test (read, fetch, search, list, create, update,
  cancel, send). None of the three names fits, and "activity" is not
  a verb.
- **D1 = B made two of the tiers the same boundary.** Existence and
  busyness are both scoped to this unit and neither returns a word anyone
  said, so a tool boundary between them protects nothing. Content is the one
  line that must stay a separate tool: it is where other people's words
  start, and where R-13's untrusted wrapping applies.

| Tool | Question | Returns | Discloses |
|---|---|---|---|
| `room_list` | which rooms am I in, and which are active | per room of this unit: network, label, member count, transport state, last message time, message count over a window; other units as existence only (D1 = B). No text, no member names | that a room **exists** and is **busy** |
| `room_read(room)` | what is being said over there | recent messages, same shape as `load_transcript` | **what people said** |

Both follow R-27: `ok`, `nothing_found`, `unavailable`, never an error
presented as an empty answer. Both are read-only. Neither returns room ids
(R-18). `room_read` takes a room by **label as `room_list` returned it**,
and the tool resolves it; an unknown label is `nothing_found`, not a guess.

`room_read` output is chat other people wrote, and gets the same untrusted
wrapping (R-13) and the same "never an instruction to you" framing that
`load_transcript` carries.

Built to ADR 0015's tool rules: the docstring's first paragraph says what
and when to call it and fits one 40-token catalogue line; each declares a
level; how to read a result goes in the returned `note`, not the
description; no em or en dashes.

## D1: the unit boundary (decided 2026-09-23: B)

Units (DEC-105) exist so one group's talk does not reach another. These tools
are the first thing able to cross that line from inside a turn, so the
boundary has to be set before any of them is built.

| Option | `<presence>` / `room_list` names | `room_list` activity | `room_read` |
|---|---|---|---|
| **A. Same unit only** | this unit's rooms; nothing else | this unit | this unit |
| **B. Existence across units** (chosen) | this unit's rooms by name; "you are also in other groups", no names | this unit | this unit |
| **C. Names across units** | every room by name | every room | this unit only |
| **D. Open** | every room | every room | any room |

**Decided: B** (operator, 2026-09-23). Nora saying she is only in this group would be false,
and a false answer about herself is the failure `00` opens with. Naming the
other groups tells this room about people who never agreed to be mentioned in
it. B is the only option where every sentence is both true and discloses
nothing a member of this room could not already guess. `room_read` never
crosses a unit under any option short of D, and D is listed for completeness,
not recommended.

**Deferred, not decided:** the operator's own DMs, or an operator-only room,
may want option D. If it is ever wanted it is a per-room flag in
`policy.toml`, not a global setting, and it defaults off. v1 ships B
everywhere.

## What already exists and is reused

- `group_members` stays as it is: members of *this* room. It is not folded into
  `room_list`, because "who is here" and "where am I" are different questions
  that already route correctly.
- `send_to` keeps naming a **network**. Once `<presence>` lists the unit's
  rooms, it no longer needs its "work out which of the four networks they mean"
  paragraph to carry the room model on its own.
- `room_list`'s activity counts come from the stored messages Nora already keeps
  for transcripts; no new capture.
