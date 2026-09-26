---
type: design
title: "The Note Dreamers Are Research Loops — each decides what to read; code decides what may be written"
description: "Operator direction 2026-09-26: note-candidates, note-curation and note-merge stop being tool-free judges fed a context code chose, and become research loops that pull what they need through read-only tools under a budget. Code keeps every write-side guard. The unit-keyed passes get a note-scoped read bound to a note's own origin room, which makes no new disclosure. Supersedes design 37 §6 (tool-free judges), design 42's S5a/S5b/Stage B, and turns DEC-203's lapse rule into a fallback."
tags: [nora, note-tier, dreamer, research-loop, tools, note-candidates, note-curation, note-merge, disclosure, eval]
timestamp: '2026-09-26'
---

# 43 · The note dreamers are research loops

## 1. The decision

**A Dreamer is not a synthesizer.** Until now each note pass was a
tool-free judge (design 37 §6: «a judge handed a search tool becomes a
researcher»): code chose a context (a batch, a 40-row evidence window, the
notebook) and the model judged only what it was handed. Every recent
failure came from the context being wrong, not from the judgement:

- The curator's newest-40 window could never reach back to a note in a busy
  room, so past-date notes stood forever. DEC-203 patched it with a two-day
  clock rule.
- note-candidates saw one hour of messages. Design 42 planned to push more in
  by code: a 40-row lead-in (S5a), Standing (S5b) and one fixed memory
  question (Stage B). S5a then measured nothing on the recall cases
  (0/5 → 0/5), because the context code chose was not the context the misses
  needed.

**The rule from here: the model decides what to read; code decides what
may be written.** Each note pass gets read-only tools and a budget, and
pulls what the case in front of it needs. Every write-side guard stays in
code, unchanged: the body schema and its validator, carry-forward, the
new-evidence rule (#590), and the grounding of a retirement in cited
messages.

## 2. What each pass is handed, and what it may pull

| pass | keyed on | handed by code (the trigger and its minimum) | may pull (read-only tools) |
|---|---|---|---|
| note-candidates | the room | the new messages; the notebook index (titles, summaries, dates) | `load_transcript` (this room, earlier); `ask_memory` (this room's people); `note_read` (a note's body) |
| note-curation | the unit | the notes due for a verdict (passed anchor, due revisit), with their bodies | `note_evidence` (a note's own origin room, around its date); `ask_memory` scoped the same way |
| note-merge | the unit | the unit's open notes with their bodies | `note_read`; `note_evidence` for a candidate pair |

note-candidates is room-keyed, so the room tools it needs already pass
`tool_subset_faults` (`nora/agent/dreamer.py:548`, design 33 §4.1). The two
unit-keyed passes are refused room tools there on purpose: a unit has no one
room, and binding a tool to an arbitrary room of it would be a disclosure
decision nobody made (R-31).

**`note_evidence(note_id, around, before, limit)`** resolves that. It reads
only the note's own `established_in` room: the room whose conversation the
note came from, which the tier has already read. So it discloses nothing new.
Its window is centred on what the caller asks for (the note's creation, its
anchor, a date named in its body), not on "the newest N". This is the read
the curator's busy-room problem needed. A room other than the note's own is
refused, and counted.

## 3. Grounding stays in code

A citation must name a message the pass actually saw. "Saw" now means shown
in the prompt OR returned by a tool in this pass. The runtime records every
row a tool returns into the pass's shown set, which `accept()` and `_ground`
already consult. So:

- A curator `closed` still needs a cited id; it may now be one `note_evidence`
  returned.
- `expired` still needs the clock (`anchor_passed`). The coverage rule becomes:
  the pass looked across the anchor (a `note_evidence` read spanning it) and
  found no answer. That replaces DEC-203's two-day lapse, which remains the
  fallback when no read was made.
- note-candidates' body citations and `message_ids` may cite loaded rows.

A tool the model did not call proves nothing. Absence of evidence is still
only `expired` when the read that would have held it was made.

## 4. Budgets and cost

Each pass gets a tool-call cap and the existing loop budget (`[dreamer.budgets.*]`):

- note-candidates: at most 3 tool calls per fire. Most batches are chatter and
  should call none.
- note-curation: at most 2 per note judged, capped per tick
  (`max_revisits_per_tick` already bounds the notes).
- note-merge: at most 4 per pass.

`[dreamer] max_tokens` (28000) stays below the loop output budgets. Each budget
is sized from the measured emission after the first eval, not from n=1
([[never-size-a-ceiling-on-n-1]]). The cost read-back is calls per fire,
tool calls per fire, and output tokens.

## 5. What this supersedes

- **Design 37 §6** ("the passes stay tool-free judges"): reversed for all
  three note passes.
- **Design 42 S5a (#592, held), S5b and Stage B**: dropped. The lead-in becomes
  `load_transcript` on demand, Standing is read by the pass if it needs it,
  and the fixed memory question becomes `ask_memory` on demand. Design 42's
  schema, restatement and guards (DEC-213) stand unchanged.
- **DEC-203**: the two-day lapse becomes the fallback. The primary expiry
  ground is a `note_evidence` read across the anchor.
- **Kept**: note-merge's one sample, overlap/cross-room refusals and the §7.8
  eval gate before `on`, and the curator's retire-only posture.

## 6. Build and measure: one pass at a time

1. **note-candidates** (room tools exist; the smallest change). Grant the
   tools, add the cap, extend the shown set with tool-returned rows, and add
   an objective paragraph on what each tool is for (what it reads, not when to
   use it). Eval, master vs branch: the recall cases p12/p13, p11 and the
   negatives. Invention must not rise; recall and wrong-note rates are the
   reading. Cost per fire is reported.
2. **`note_evidence`** (a new unit-safe, note-scoped read tool), then
   **note-curation** on it. Eval: busy-room expiry cases, a note answered
   long before its date (it must close, not expire) and a rescheduled plan
   (it must stay open).
3. **note-merge** on `note_read` + `note_evidence`. Eval: design 41 §7.8,
   unchanged, before `on`.

Each step ships alone and is read back live (tool calls per fire, refusals,
cost) before the next.

## 7. Open positions

| position | what would decide it |
|---|---|
| the caps (3 / 2 per note / 4) | measured tool calls per fire after step 1; a cap that binds on real cases is raised |
| `ask_memory` in the unit-keyed passes | whether Honcho's workspace scoping can be bound to a note's room; if not, it stays with note-candidates only |
| whether S3's "every open note with its body" stays, or shrinks to the index plus `note_read` on demand | step 1's eval; one variable at a time, so S3 stays as shipped until measured |
| DEC number | taken when step 1 merges |

## Status / Checkpoint

**2026-09-26 — design only.** Operator direction, verbatim in substance:
«ALL note-* dreamer, they are research-loop, they should decide to gather the
information they NEED instead of we decide what to feed them … it's a
"dreamer" NOT a synthesizer.» Nothing is built.

Related: design 33 §4.1 (read-only awareness), 37 §6 (reversed), 41 (curator,
note-merge), 42 (format; S5a/S5b/Stage B dropped), DEC-203, DEC-213, #590, #592.
