---
type: design
title: "Intent: a sessionless agent keeps her process, not a summary of it"
description: "Why the folded paragraph loses the part of a turn that matters most, what the operator asked for, and the one sentence the rest of the bundle serves."
tags: [nora, standing, sessionless, process, decisions]
timestamp: '2026-10-25'
edges:
  - {dst: 10-what-standing-holds, rel: sequences, provenance: asserted}
---

# 00 · Intent

## 1. The problem, as the operator put it

Nora runs sessionless. A tag turn, a fire turn and a Cognition wake each start
from an assembled prompt; nothing of the previous turn's loop carries into the
next except what was written somewhere the next prompt reads. Reasoning is
carried only between the model calls of ONE turn
(`nora/agent/reasoning_carry.py`), so turning thinking on or off has never
changed what she remembers across turns. Standing does.

What reaches the next turn today:

| what | reaches the next turn as | measured or read in the tree |
|---|---|---|
| the room's words | `<group_transcript>`, the newest 20 rows by default (`StoreConfig.effective_prompt_window`, `nora/config.py`) | the operator's own observation: she loses a topic after 10 to 20 messages |
| her reply | one transcript row, bare text, no provenance | DEC-112 |
| her tool results | nothing in the prompt; `recall_lookups` can read back the `cacheable` ones for 7 days | `nora/agent/tools.py`, `nora/infra/sql/tool_call_log.py` |
| her reasoning, comparisons, scores | nothing | `reasoning_carry.py` scope |
| her position | Standing: `Where it stands`, one paragraph capped at 1200 characters, rewritten after every tag turn by one fold call (a second when the first overflows the cap) | `nora/domain/standing.py`, `nora/engagement/stages/debrief.py` |
| fire turns' work | only their `cacheable` tool calls, through `recall_lookups`; the fire path discards its `TagTrace`, so the rest of the exchange is gone and no debrief runs | `nora/app/fire.py` (the comment at the `TagTrace` hand-back) |

So the turn's OUTPUT survives (it is in the transcript) and its PROCESS does
not. A deep round that scored two venues and ruled one out writes, at best, a
sentence into a paragraph that the next fold will rewrite again. Each fold
summarises the previous summary. That is generational loss, and no syntax fixes
it while the fold's job is "rewrite the paragraph".

## 2. What the operator asked for

- Standing is the **process and the decision log**: what is open, what she is
  leaning toward and why, what was decided and on what grounds.
- **Not execution items.** Which tool ran in which order is not worth carrying;
  it is in the turn's record if it is ever needed.
- **The output is already in the transcript**, so Standing must not restate it.
- **Hybrid**: some of it written by code, some by the model.
- **Retire `recall_lookups`.** A well-run Standing that cites its evidence makes
  a separate "what did I look up" tool unnecessary.

## 3. The sentence the bundle serves

**Standing holds what cannot be re-derived from the Record, each line citing
what it rests on, and is revised by id rather than rewritten.** Everything else
in this bundle is how that sentence is stored ([20](20-the-ledger.md)), what
it cites ([30](30-turn-records.md)), how it is measured
([40](40-verification.md)), and the order it ships in
([50](50-migration.md)).

## 4. Out of scope

- **Cross-room visibility as a feature.** Unit-scoped Standing is the shape it
  will take ([20](20-the-ledger.md) §6), but the room topology change that
  lets a unit hold several rooms per platform is its own bundle.
- **The preference model and ranking** behind recommendations. Its output is a
  decision in the shape of [20](20-the-ledger.md) §2, which is the only thing
  this bundle fixes about it.
- **Person Standing** ([12-schemas](../cognition-loop/12-schemas.md) §5) keeps
  its sections; the private-dm bundle owns when it is written.
