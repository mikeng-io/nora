---
type: design
title: "The proactive turn reviews the situation — the reminder is a reason to look, not the thing to say"
description: "Operator direction 2026-10-12, after three near-identical proactive asks reached one room within about an hour on 2026-10-11, the last two after the group had already met. The loop stays three pieces: Cognition notices and writes a reminder; the reminder fires; the proactive turn looks at the whole situation and replies, acts, or does nothing. The defect was the third piece: it was handed the reminder as a request and never shown where people were. The fix is what that turn reads and how it is framed, not a guard. No dedupe, no screen, no new refusal."
tags: [nora, cognition, proactive, fire-path, context, presence, rollup, colocation, silence, eval]
timestamp: '2026-10-12'
edges:
  - {dst: 30-cognition, rel: extends, provenance: asserted}
  - {dst: 25-rollup, rel: depends_on, provenance: asserted}
---

# 35 · The proactive turn reviews the situation

## 1. The shape, and it stays this simple

There are three pieces and only three:

1. **Cognition notices.** It wakes, reads what changed, and when something
   may need doing it writes a reminder (`remind`, [30](30-cognition.md) §act).
   It is the subconscious: it never speaks.
2. **The reminder fires** at its time and opens a proactive turn in the
   room (`FirePath`, `nora/app/fire.py`).
3. **The proactive turn reviews the situation** — what was said, by whom
   and when, what she herself already said, where people are — and then
   replies, takes some other action, or does nothing. It is the conscious
   layer, and it owns the judgement.

The judgement "is this still worth saying" belongs to piece 3, because only
piece 3 runs at the moment it matters. Piece 1 decided hours or minutes
earlier from what it knew then. So:

- **Cognition may write a reminder that turns out stale, or two that overlap.**
  That is fine and expected. Each one fires into a turn that looks again.
- **No guard sits between the pieces.** No fire-time dedupe, no remind-time
  overlap refusal, no new screen. If piece 3 sees the whole situation and
  still speaks wrongly, that is a context or framing defect in piece 3, and
  it is fixed there.

## 2. What happened on 2026-10-11

Three proactive rows for one plan in the Friends room fired within about an
hour. Each produced a near-identical ask about where to meet. Nora had
already answered that question herself in the room before the second fired,
and by the second and third the members were physically together.

Piece 3 had the transcript. It still asked again, for four reasons, all on
its input or its framing (files on master as of this document):

| # | What the turn was missing or told | Where |
|---|---|---|
| 1 | The reminder text arrived as the last user message in `<tag_message>`, the block every tag turn uses for *a person's words waiting for an answer*. It read as a request. | `nora/app/fire.py` `_turn`; `nora/agent/prefill.py` `build_tag_message` |
| 2 | Nothing in her context said where anyone was. `<presence>` is her own network state, not the members'. Member location exists only through `where_is`, whose description says "call this when somebody asks". Nobody asked, so she never looked. | `nora/language/presence_render.py`; `nora/agent/tools.py` `where_is` |
| 3 | The turn is framed to reply: the closing line is "I'll read the conversation and reply as myself", and `send_reply`'s description calls ending without a send "an emergency fallback, never a choice". Doing nothing is permitted by the header but described as a failure by the tool. | `nora/domain/framing.py` `CLOSING_LINE`; `nora/agent/tools.py` `send_reply` |
| 4 | The turn runs at `reasoning_effort = "none"` (the participation setting, chosen because a speak-or-not call "does not get better for being deliberated"). Reconciling an earlier intention against a newer transcript is not that call's shape. | `nora/config.py` `[participation] reasoning_effort` |

Not a cause: the overlapping rows themselves (piece 1 behaving as designed,
§1), and the absence of a dedupe guard.

## 3. The design: what the proactive turn reads

Four changes, all to piece 3's input and framing. Each is one variable and is
measured on its own (§5).

### 3.1 The reminder is her own earlier note, dated

The row's text renders as `<own_intention>`, not `<tag_message>`: a note she
wrote to herself, from what she knew at the time the header gives. Nobody in
the room said it and nobody is waiting on it. Anything in the transcript
newer than that time, her own messages included, outranks it. It is a reason
to look again.

Already built on branch `mikeng-io/proactive-own-intention-block` (held, not
merged). Measured alone at n=5 it did not move the silence cases (1/10 → 2/10,
noise), which is why it is one of four and not the fix.

### 3.2 Where the room's members are, as situation

A `<whereabouts>` block on the proactive turn: for each member of the room
that the disclosure guard lets this room see, where they are now and since
when, and who is together. Rendered from data that already exists:

- **Together:** the rollup's co-location episodes, including ones still in
  progress past `min_dwell` ([25](25-rollup.md) §As built 2a,
  `nora/domain/rollup.py` `colocations`).
- **Each person:** their latest stay, the same reading `where_is` returns.
- **Disclosure:** the same guard `where_is` uses (`Whereis.locate`, R-31). A
  member this room may not see is absent from the block, not marked hidden.

Situation, never ability: the block says "these four have been together at
X since 07:10", not "you can check where people are". Telling a model what it
is reading measured better than telling it what it can do
(design/self-awareness/01 §Framing).

Scope: the proactive turn only. Tag and participation turns are unchanged.

### 3.3 Doing nothing is an outcome, not a fallback

On the proactive turn only:

- The closing line says she will look at the situation and decide whether
  anything needs saying, instead of promising a reply.
- The header states the three outcomes as equals: say something, do something
  (a tool, a reminder move), or nothing.

`send_reply`'s description is shared with every turn and is not changed here;
if 3.3 measures null with it unchanged, that sentence is the next variable.

### 3.4 Effort

The proactive turn gets its own `reasoning_effort` instead of inheriting
`[participation]`. Default unchanged (`none`); the eval decides the value.

## 4. What this does not do

- No change to Cognition, `remind`, or the reminder rows.
- No dedupe, screen, cap, or cooldown added anywhere. `ProactiveScreen` and
  the open-note unique index are untouched.
- No change to tag or participation turns.

## 5. Measure

**Instrument:** `python -m evals proactive_act` on norty (sandbox DB per case).

**Prerequisites** (the suite cannot run the cases that matter today):

- `NoteSeed` carries `anchor_entity_ids` and passes them through `know()`
  (`nora/subject/knowledge.py`, `nora/subject/sandbox.py` `_note_seed`), so
  a07 / a04 / a08 run instead of erroring.
- `owe()` cancels any open proactive row already on the linked note, so a12
  stops colliding with seeded production rows (`nora/subject/proactive.py`).
- The suite can seed presence (stays and co-locations) for a case; without it
  3.2 has no arm to measure.

**Cases:** the existing silence cases (a03, a11, a12) plus two from
2026-10-11: the second fire (her answer is minutes old, members not yet
together) and the third (members together). One positive control that must
still speak (a01).

**Arms, one variable each, n ≥ 5 per case:** baseline; +3.1; +3.1+3.2;
+3.1+3.2+3.3; then 3.4 at `none` vs `low`. Report silence-case passes and
a01 separately; a01 regressing is a FAIL whatever the silence cases do. An
ERROR row counts as neither.

**Done** when the 2026-10-11 cases go silent at a rate clearly above
baseline across two runs, and a01 holds.

## 6. Steps

1. Harness prerequisites (§5).
2. Build the two 2026-10-11 cases; run the baseline.
3. 3.1 (exists) → measure.
4. 3.2 → measure.
5. 3.3 → measure.
6. 3.4 → measure.
7. Keep what moved the number; drop what did not.

## 7. Open

- **Freshness of co-location.** The rollup closes an in-progress stay only
  past `min_dwell`; how stale "together" can be at fire time is unmeasured.
  If it lags, 3.2 reads the latest fixes directly rather than the rollup.
- **Which members.** The room's members per the identity map; a member with
  no presence circle renders nothing.
