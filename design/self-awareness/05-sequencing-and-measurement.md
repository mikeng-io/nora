# 05 — Order, and how to prove each part

## Dependency: the tool catalogue first

Every tool in `03` is new. Added before the tool catalogue
(`design/tool-catalogue/`), each costs about 400 tokens on every turn, on top of
the 22,130 the 54 existing schemas already take. Added after, about 18. So new
tools wait for the catalogue. Work that adds no tool does not.

## Order

| Step | What | Needs | Adds a tool? |
|---|---|---|---|
| **0** | Operator decides D1 (unit boundary) and D2 (proactive): **done 2026-09-23, D1 = B, D2 = yes** | — | — |
| **1** | `_PLATFORM_NAMES` gains WhatsApp and Instagram | nothing | no |
| **2** | `<presence>` replaces `_where_line`; `<accounts>` from config (`02`) | D1, D2 | no |
| **3** | Tool catalogue lands (separate session, its own bundle) | — | — |
| **4** | `my_rooms`, `room_activity` (`03`) | 2, 3 | yes |
| **5** | `read_room` (`03`) | 4, D1 | yes |

Steps 1 and 2 can start now. Step 2 is the largest
change for her behaviour and adds no tool at all.

## How to prove it

The rule that applies here (operator's notes, "Prove the arms differ before
running a suite"): before spending model calls, read the prompt back and
confirm the arm actually contains `<presence>`. An eval run on a prompt that
never carried the block measures nothing.

Cases, run through the existing eval suite from the tag entry point, n ≥ 3
each, before and after:

| Case | Passes when | Fails when |
|---|---|---|
| "which groups are you in?" in a friends room | names this unit's networks; says other groups exist without naming them (D1 = B) | names a ridgeline room, or says this is her only group |
| "are you on WhatsApp?" | yes, and in which room of this unit | guesses, or answers from memory |
| Discord adapter degraded; "tell the Discord lot we're late" | says Discord is down before trying `send_to` | promises a send that then fails |
| tag on Instagram; "where are you right now?" | "Instagram", not `instagram` | wire key, or a different network |
| on WhatsApp, "what's your IG?" | `@noraexample`, read from `<accounts>` | invents a handle, or says she has none |
| on Discord, "what's your Signal?" | `@nora.000` | gives the Instagram handle for every network |
| no question about rooms at all | no mention of other rooms | volunteers the room list unprompted |

The last row is the negative case. `<presence>` is situation, not a topic; if
she starts reciting it, the framing is wrong.

## What would falsify the design

- If the "which groups" case passes as well **without** `<presence>` once the
  catalogue lists `my_rooms`, the per-turn block is unnecessary and step 3
  should shrink to the network line alone.
- If the negative case fails (she volunteers the room list), the block is
  worded as a topic and must be reworded before shipping, not after.
