---
type: proposal
title: "Nora Holds The Thread"
description: "She answers well and forgets. The doctrine says she is done when she has answered; the note tier holds only questions with closing conditions; nothing records whether a thing is moving; and her one way to act is to schedule an interruption. This node states the through-line the other seven implement, and names the one decision that blocks all of them."
tags: [nora, notes, proactive, participation, identity, doctrine, standing]
timestamp: '2026-09-11'
version: 1
---

# Nora Holds The Thread

## 1. The sentence this bundle turns on

> **Her value is not reminding you at the right moment. It is that she is the
> only one in the room who remembers how far a thing has got.**

Everything below is that sentence applied to a layer. If it is wrong, stop
here; nothing else in the bundle survives it.

## 2. What is true today

She is good at the half she was built for and absent from the other half, and
both halves are measured.

| | measured | reading |
|---|---|---|
| does not interrupt when she should not | false-positive **0.02**, 105 samples | working |
| acts when she should | recall **0.34**, 60 samples | not working |

That asymmetry is not an accident of tuning. It is what the doctrine says. From
`identity.md`:

> You don't linger, and you don't perform either way. You give them the thing
> they actually needed, then **you're done**.

From `silence.md`:

> **Most** turns nobody asked for end that way.

From the proactive objective:

> **Most mornings there is nothing**, and saying so is the ordinary, correct
> answer.

Three layers, one instruction: answer, then stop. The numbers are the system
working as written. **A prompt change cannot beat its own doctrine**, which is
why a night of objective rewrites moved recall from 0.25 to 0.34 and no further
— and why even that movement is inside the instrument's noise (`01 §5`).

## 3. What she cannot represent

Four scenarios, all from the operator's own rooms, none of which the shipped
design has a shape for. Stated in full in `01`; named here because each one
kills a different assumption.

1. **The wedding-gift-day dinner.** A fixed date, an afternoon that ends early,
   and no one has thought about the evening. She was *in* that conversation —
   she was asked whether they need to book and answered "it gets busy, expect a
   queue" — and then the thread died with her own answer unacted-on. Kills:
   *she notices when a thread stalls*.
2. **The game the room re-argues from zero.** Every few weeks: someone asks
   what to play, someone rejects a proposal, it stalls, and by the time the
   current game is finished they take whatever is nearest. Each rejection was a
   constraint being stated out loud, and nobody keeps them. Kills: *rejections
   accumulate into anything*.
3. **A recommendation with no event behind it.** "You may still be on the last
   one — this came out a few days ago, it might suit you." Nothing was asked,
   nothing is due. Kills: *a turn needs a trigger from the room*.
4. **A friend's sprained ankle.** Photos, then acupuncture photos days later.
   Worth a word when it comes up naturally, and worth nothing at all as a
   scheduled 18:00 interruption. Kills: *everything worth saying is worth
   scheduling*, and *a note is an unsettled question*.

## 4. The four things that follow

- **A thing dies from stalling, not from missing a deadline** (1, 2). The
  signal she needs is *when did this last move*, and it exists nowhere: `Note`
  has `created_at` and no last-moved, and neither note renderer shows one.
- **Progress is a chain of small pushes, not one moment** (1, 2). Each push
  only becomes possible once the previous lands. "When" is therefore *when is
  the next inch possible*, not *when is the deadline*.
- **Some live things are states, not questions** (3, 4). "They are playing X
  and nearly done", "his ankle is hurt" — no `uncertainty`, no
  `closing_condition`, so the note tier cannot hold them by definition. And a
  state is exactly what makes an outside fact relevant: without *they are
  playing X and nearly done*, a new game release is spam.
- **Not everything worth saying is worth scheduling** (3, 4). Her only act is
  `remind(when, text, note)`, so every noticing becomes a scheduled
  interruption or is lost. Both turns already read the same note store — the
  missing exit is a field, not a pipe (`04 §4`).

## 5. Late and early are not symmetric

Work has deadlines that force action, and being early is waste. Friendship has
no deadline: the "deadline" is the occasion itself, and by then it is over.
The cost of being early is one unnecessary sentence. The cost of being late is
that the thing never happened, or happened badly because it was rushed.

**This asymmetry must be written down, not left to judgement.** A system told
to "choose the right moment" under a doctrine that says "most of the time,
nothing" will choose nothing. `02 §4` states where it goes.

## 6. What this bundle decides

Seven layers, top-down, because the top one gates every one below it.

| node | layer | change policy |
|---|---|---|
| `02` | identity, then doctrine | **operator-only**, then agent-may-edit |
| `03` | what a note IS — shape, boundary, responsibility | schema |
| `04` | last-moved, and the two exits | renderer + schema |
| `05` | Standing: read-only on the fire path, per-room not per-thread | structural |
| `06` | the writer, and the instrument | adaptor + evals |
| `07` | decisions, order, blast radius | — |

## 7. What blocks it

**DEC-163 gates the bundle.** `identity.md` is `change_policy: operator-only`,
and it is the file that says "then you're done". Until that sentence is
decided, every change below it is a change that the doctrine above will
overrule at runtime — which is precisely what tonight's measurement showed.

Second, and independently: **nothing here can be shown to work.** Issue #158
records that `proactive_decide` recall swings 0.20–0.50 on an *identical*
prompt at n=5, and there is not one case in the suite for a dateless-but-live
note, a state, or the participation exit. `06 §4` makes the instrument a
precondition rather than a follow-up, on the grounds that this repo has already
paid for the alternative twice.

## 8. What this is not

- Not a new tier. Every mechanism needed already exists — revisions, retirement,
  a per-workspace index, two turns reading the same store. What is missing is
  what those mechanisms are *allowed to hold*.
- Not a relaxation of R-14 or the anti-nagging rules. A thing already answered
  has no gap left to close, and that is the same test, not a weaker one
  (`03 §6`).
- Not licence for the note writer to create more. `06 §2` argues the trigger
  must be fixed *before* the shape is widened, because widening what a
  mis-triggering writer may write makes it worse, not better.
