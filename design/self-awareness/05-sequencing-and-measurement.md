# 05 — Order, and how to prove each part

## Dependency: the tool catalogue first (withdrawn 2026-09-24)

This section first said new tools wait for the tool catalogue: added before
it, each costs about 400 tokens on every turn; added after, about 18. The
catalogue was then measured and stays OFF
(`design/tool-catalogue/05-measurement.md` §3a): deferring schemas moved input
from cached to uncached (10.3k to 19.5k uncached tokens a turn) and added
about 1.4 model calls a turn, so `[llm] defer_tools` is `false` by measured
decision. There is nothing left to wait for. The room tools are built with
every schema resident, and their cost is their own description tokens
(`07-room-tools-plan.md`), which ADR 0015 keeps at Level 1 and 2.

## Order

| Step | What | Needs | Adds a tool? |
|---|---|---|---|
| **0** | Operator decides D1 (unit boundary) and D2 (proactive): **done 2026-09-23, D1 = B, D2 = yes** | none | no |
| **1** | `_PLATFORM_NAMES` gains WhatsApp and Instagram: **done** | nothing | no |
| **2** | `<presence>` replaces `_where_line` and proactive's `home_room`, handles included (`02`, `06`): **done** | D1, D2 | no |
| **3** | `room_list` (`03`, `07`) | 2 | yes |
| **4** | `room_read` (`03`, `07`) | 3, D1 | yes |

Step 2 is the largest change for her behaviour and adds no tool at all.
Steps 3 and 4 ship together (`07`).

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
| on WhatsApp, "what's your IG?" | `@noraexample`, read from `<presence>` | invents a handle, or says she has none |
| on Discord, "what's your Signal?" | `@nora.000` | gives the Instagram handle for every network |
| no question about rooms at all | no mention of other rooms | volunteers the room list unprompted |

The last row is the negative case. `<presence>` is situation, not a topic; if
she starts reciting it, the framing is wrong.

## What would falsify the design

- If the "which groups" case passes as well **without** `<presence>` once
  `room_list` exists, the per-turn block is unnecessary and step 2
  should shrink to the network line alone.
- If the negative case fails (she volunteers the room list), the block is
  worded as a topic and must be reworded before shipping, not after.
