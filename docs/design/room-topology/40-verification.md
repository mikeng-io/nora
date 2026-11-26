---
type: design
title: "Verification: the routing eval, tool_selection, and the policy and relay tests"
description: "A send_to routing corpus with clear, ask and refuse cases, measured on master and on the branch; tool_selection for the new argument; the unit tests that pin the new invariant and the unchanged gates."
tags: [nora, topology, eval, routing, send-to, tests]
timestamp: '2026-10-25'
edges:
  - {dst: 20-addressing, rel: measures, provenance: asserted}
---

# 40 · Verification

## 1. Routing corpus (new)

Cases set in a unit with a general Signal room, a dinner Signal room and a
Discord channel, each with a description. Run on master (where the unit holds
one Signal room, as the former rule required) and on the branch, with enough
cases that a difference is not noise (`docs/eval-sop.md`).

| kind | example | pass |
|---|---|---|
| **clear by purpose** | dinner talk on Discord, "send it to Signal" | `send_to` the dinner room |
| **clear by name** | "tell the general group" | the general room |
| **network, one room** | in a unit with one WhatsApp room, "tell WhatsApp" | that room, as today |
| **ask** | an unrelated remark, "send it to Signal" | she asks which room; no send |
| **after asking** | the answer to her question | the named room |
| **refuse** | a room of another unit, a name nobody uses | the unknown refusal; no send |
| **not allowed** | the asker is not in the destination | R-49's refusal |

The ask cases are scored in both directions: asking when the answer was clear
is a fail, and so is guessing when it was not.

## 2. `tool_selection`

`send_to`'s argument changes from a network to a room, and its docstring is
rewritten, so its routing is measured before and after (ADR 0004, ADR 0015),
with `room_list`, `room_read` and `send_reply` as siblings in `not_tools`.

## 3. Tests

- A unit with two rooms whose names fold to the same string is refused at
  startup, naming both; so is a label equal to a network's name on a network
  with two or more of the unit's rooms, and on another network's room.
- A unit with two labelled Signal rooms loads: **at step 3**, once the
  one-per-platform refusal is gone (until then that refusal rejects it by
  design).
- A room with no label is still refused, as today.
- `send_to` resolves name, then a unique network; returns `ambiguous` with
  the candidates' names for a network with several; returns the same unknown
  refusal for another unit's room and for no room.
- A send between two rooms on one platform is not refused as the same room;
  a send to the source room itself is.
- R-49's authorisation gates are unchanged, and their tests pass as they are.
  The addressing tests change with the address: the multi-candidate case in
  `tests/test_relay.py` that expects `refused` (`ambiguous_room`) expects
  `ambiguous` with the candidates' names.
