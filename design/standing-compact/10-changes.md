---
type: contract
title: "Changes — one call, a clock on the prior, the turn's steps at full size"
description: "What is deleted, what the fold is shown, and what its instruction is told about what it is reading."
tags: [nora, standing, debrief, render, verdict]
timestamp: 2026-09-24
edges:
  - {dst: 00-intent, rel: realizes, provenance: asserted}
---

# Changes

## 1. Deleted: the verdict, and both re-derivation triggers

| deleted | where |
|---|---|
| the verdict call, its objective (`standing-verdict.md`), its parse, the deterministic room-scope claim that only fed it | tag path `nora/engagement/stages/debrief.py`, event path `nora/ingest/stages/standing_event.py` |
| `render_verdict_input`, `render_rollup_verdict_input` | `nora/engagement/debrief_render.py` |
| `TurnVerdict`, `TurnVerdictStore`, `SqlTurnVerdictStore`, the fake and its contract suite | domain, port, infra, testing |
| `_should_rebuild`, both triggers (`ungrounded`, `ceiling`), `[standing] rebuild_after_folds` | debrief stage, config |
| `Standing.consumed_verdict_tag` (the watermark of the ungrounded trigger) | domain, SQL store |
| `nora_turn_verdict_total`, `nora_standing_rederivations_total`, `nora_standing_rederivations_written_total`, `nora_debrief_room_scope_overrides_total` | metric specs |

**The fold always reads, and always is shown, the prior.** There is no longer a
path on which the prompt says "no position yet" about a conversation that has
one. `fold_count` stays on the row as a plain count. Nothing resets it.

**The drift the ceiling bounded is handled by §2 instead.** The ceiling existed
because a position revised in place can ratchet: clauses survive because nobody
removes them (#78). Its remedy was to throw the position away every N folds,
which removes the healthy clauses along with the stale ones. The measured stale
clause, "tomorrow" 17 days on, survived because the fold could not tell it was
stale. So the remedy is to give it the time, not a periodic blank.
*The reading that would reopen this:* positions pinned at the character cap with
clauses the fold keeps re-affirming across more than ~20 folds after §2 ships.

**Schema unchanged.** `turn_verdict` and `standing.consumed_verdict_tag` stay
in the database. Code goes, schema stays, as with the fact tier.

## 2. The prior carries when it was written

`<standing_prior>` gains `written`, rendered as an age (`nora.domain.prompt.age`,
the same vocabulary `<standing>` already uses in the tag turn) against the
turn's own time, on both the tag path and the event rollup. The block type
annotates it: *"how long before this fold you wrote it: a relative time inside
it (tomorrow, tonight, now) meant that relative to then, not to now."*

The instruction gains one sentence in the same register: keep a time as a date,
never as a relative word, because the next fold reads it days later.

## 3. The turn's steps, as they are

**Renamed `<reasoning>` → `<steps>`.** The description says what the block
holds: her own steps, what she called and what came back. It says that **a
tool's result exists only here**: anything in it that she does not carry into
the position is gone after this turn.

**Private reasoning is marked when present, and only then.** A step built from a
`reasoningContent` block renders with `kind="thinking"`, and the type declares
what that attribute means. On a model that produces none, no step carries it and
the block says nothing about reasoning. On a model that does, the same render
shows it with no code or instruction change.

**Tool results are shown at up to 4000 characters, not 600.** Measured on 1000
tool calls in production (2026-09-09 to 09-24): p50 277, p75 856, p90 2552, p95
7555. At 600, more than half of `note_read`, `search_memory`, `ask_memory`,
`web_fetch` and `about` results were cut, and 37 of 39 `web_search` results.
4000 carries `web_fetch` (p90 3867) and `ask_memory` (p90 3757) whole, and the first ~40% of a median `web_search`. Thinking steps take the
same bound. The model's own narration and tool CALLS keep 600: neither has
content that exists nowhere else. The clip is still counted and marked (#189).
Worst case for the fold's input, a turn at the tool-call budget, is bounded by
the loop's own `tool_calls` budget, not by this render. `[standing]
timeout_seconds` still bounds the call.

## 4. The instruction: what belongs

One bullet is added to *what belongs*, in the same list and register as the
others. It is not a menu of cases:

> what I found or worked out on this turn and have not yet given the room, and
> when I found it, while it is still useful to someone here.

The existing exclusions stand: anything that must survive goes in a note, and
no addresses, numbers or ids. A restaurant's name is a finding. Its street
address is not.

The intro's description of the input changes from "your own reasoning and tool
calls (`<reasoning>`)" to "your own steps on the turn (`<steps>`)", and says that
the prior carries when it was written.

## Status / Checkpoint

2026-09-24: written with the implementation on branch
`mikeng-io/disable-relevant-gate-and`.
