---
type: design
title: "Intent — standing is the room's compact"
description: "What standing is now that the turn carries no reasoning, what the read-back of 20 turns showed, and why the verdict goes."
tags: [nora, standing, debrief, compact, verdict]
timestamp: 2026-09-24
edges:
  - {dst: 10-changes, rel: sequences, provenance: asserted}
  - {dst: 20-verification, rel: sequences, provenance: asserted}
---

# Intent

## What standing is

A turn is an agent loop: the model reads, calls tools, reads what they
returned, and replies. When the turn ends, everything the loop held that did not
reach the room is gone. The next turn starts from the transcript, which records
what was SAID, and from nothing else.

**Standing is the part that survives: the compact of her work in one room.** It
is the same thing a long-running coding agent writes when it compacts its
context: not a transcript summary, but the state a successor needs to carry on.
Three kinds of thing go in it, and only these three:

| | example | why it is not somewhere else |
|---|---|---|
| **what she is holding open** | a question she asked and is waiting on; a thread she decided to drop | the transcript shows the question, not that she is still waiting on it |
| **what she owes** | she said she would check whether a server is public | a promise attributed to her in the transcript is a member's claim; this is her own word |
| **what she found and has not given the room** | a search returned eight restaurants, she sent two | the tool result lives only inside the turn; nothing else keeps it |

The third row is new. It is what the operator asked for: *"a few searches
found restaurants but did not send them all; can that stay in standing?"* Before
this bundle it could not, for a mechanical reason: the fold was shown each tool
result cut to 600 characters, and a `web_search` result is 9.6k at the median
(measured, 1000 tool calls, 2026-09-09 to 09-24). The fold never saw the other
six restaurants.

Standing stays **lossy and rewritten every turn**
([26 §8](../dreamer-agent-loop/26-standing.md)). A decided venue still belongs in
a note. The working set is exactly what a lossy row is FOR: useful for a few
turns, harmless to lose, and dated so the fold can tell when it has gone stale.

### Where reasoning used to be

The fold's instruction said it was given "your own reasoning and tool calls".
The deployed model produces no reasoning content, so on every turn in the
read-back the block called `<reasoning>` held tool calls, tool results and the
text she wrote, and nothing else. The name asserted a thing that was not there.

The operator's instruction: *make it depend on whether there is reasoning.* So
the block is renamed to `<steps>` and describes what it holds, and a step of
private reasoning is marked as such **when the model produced one**. The
instruction does not change when a reasoning model comes back. The render does,
by itself.

## What the read-back showed

Twenty engagement turns, 2026-09-22 to 09-23, read from Langfuse along with
every `turn_verdict` row in the database.

**The verdict does not discriminate, and when it fires it is usually reading a
fragment.** All-time: 381 `grounded=true`, 25 `false` (406 turns). In the 20-turn sample,
19 true and 1 false. The false one flagged her own public handles as "no
source". They were in `<presence>`, which the tag turn reads and the verdict
call does not. Of the 25 all-time, **17 (68%) came from a turn with a tool
result longer than the 600 characters the verdict was shown**, against 95 of
381 (25%) for the grounded ones; 12 of the 25 turns called `web_search` or
`web_fetch`. The typical flag is a fact "not in the tool result", from a result
the judge saw a sixteenth of. Issue #189 predicted this: the judge grades a
truncated view and cannot know it. (Joined on `tag_trace`, which retains all
406 verdict turns.)

**Its only consumer destroys the position.** An ungrounded verdict triggers a
re-derivation on the next turn. A re-derivation withholds the prior from the
prompt, and the prompt then tells the model *"no position yet — this is the
first fold for this conversation"*. In the sample, the turn after the false
verdict wrote a 145-character position over a 435-character one. Three threads
she had been holding for days were dropped because a judge could not see
`<presence>`. The "re-derivation pass" that the verdict's docstrings say reads
the table does not exist.

**The fold-count ceiling does the same thing on a clock.** It fires at
`[standing] rebuild_after_folds` (20 in production) and blanks the prior in the
same way.

**The event path runs the verdict and throws it away.** `standing_event.py`
gathers a verdict call beside its fold and persists nothing from it: a model
call per rollup with no reader.

**Standing carries claims with no time.** On 09-23 one room's position still
said *"today (6 September)"* and *"tomorrow at 10, owed"*: 17 days stale, and
re-affirmed on every fold. The fold is told to re-judge every inherited clause,
but the prior it reads carries no date, so it cannot tell that "tomorrow" was
two weeks ago. This is the Relevance Gate's defect
([AGENTS.md](../../AGENTS.md), *"when the measurer is a MODEL, its input has to
explain itself"*): a judgement that turns on *when*, over an input with no
time. `<standing>` in the tag turn already renders `written` as an age.
`<standing_prior>` in the fold never did.

## The claim

1. Deleting the verdict and both re-derivation triggers loses no signal anyone
   reads. It removes the one path that throws the position away, and one model
   call per event rollup.
2. Rendering when the prior was written, and telling the fold to write dates
   rather than relative time, lets the fold age a stale clause out instead of
   re-affirming it.
3. Showing the fold the turn's tool results at a size that carries them, and
   telling it that those results survive only through what it writes, lets it
   keep unsent findings. Nothing else changes about what belongs in standing
   — but every reader of standing (the tag turn's `<standing>`, proactive
   decide, culture, and the Relevance Gate when it is on) now also reads
   those findings, and 20-verification §3 watches for that.

What would falsify each is in [20-verification](20-verification.md).

## Not in scope

- **#222's repair**: revision provenance (`instruction_sha256`), a fail-closed
  read TTL, a trigger that is not the tag path. Untouched here, still open. The
  dates this bundle adds are *inside* the prose. A read-side TTL is a separate
  property.
- **Reply grounding.** Whether her REPLY stated something no source supports is
  a real question. A per-turn production call whose only consumer blanks the
  standing row is not the instrument for it. That belongs to the eval suite
  (`evals/rebuilt/rubric.py` already grades grounding against the given
  blocks).
- **The `turn_verdict` table.** The code goes, the schema stays. Dropping a
  table is a separate explicit decision, as it was for the fact tier.

## Status / Checkpoint

2026-09-24: accepted by the operator the same day the read-back was presented.
Measured the same day (20-verification §5): claims 1 and 2 hold (carry 6/12
→ 12/12, stale 1/12 → 8/12, recap unchanged at 6/6); claim 3 does not yet
hold (residue 0/9 on both arms), and #445 tracks it.
