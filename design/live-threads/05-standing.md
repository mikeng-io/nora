---
type: evidence
title: "Standing Is The Only Clock, And It Stops When She Stops Being Tagged"
description: "Standing is lossy by design, per-room rather than per-thread, and — verified in the tree and documented in it — never written by the fire path. So the quieter a room gets, the staler her position, and that is the room most in need of her."
tags: [nora, standing, debrief, participation, proactive]
timestamp: '2026-09-11'
version: 1
---

# Standing Is The Only Clock, And It Stops When She Stops Being Tagged

## 1. What Standing is

One row per `(platform, conversation_id)`, revised in place by the debrief
stage. From its own module docstring:

> **It is a POSITION, not a record** ... Standing is *what Nora currently thinks
> about this conversation*, rewritten every turn and **lossy by design** (26 §8)
> — which is exactly why nothing that has to survive may live here.

## 2. Finding 1 — it cannot be the accumulator, and that is correct

`01 §4.2` needs the room's stated refusals to survive weeks. Standing rewrites
in place and is capped at 1200 characters, so anything not restated is gone.

**Not a defect.** It confirms the routing in `03 §2`: durable refusals are
memory, the current state is a note, and the position is neither. Recorded so
nobody proposes Standing as the home for accumulation.

> **Re-read 2026-09-22, after the operator took DEC-163.** Two numbers in
> this section moved between writing and merge, and neither changes the
> finding.
>
> - **The ceiling is 4,000 characters, not 1,200.**
>   `STANDING_BODY_CEILING_CHARS = 4000` (`nora/domain/standing.py:46`),
>   which `standing_body_fits_the_ceiling_check` reads rather than
>   hardcodes (`nora/infra/sql/schema.py:2218-2221`). A bigger cap makes
>   the position hold more of a busy room, not a different kind of thing.
> - **History is no longer lost — the HEAD still rewrites in place.**
>   `standing_revision` landed in #352 (migration `0059_standing_revision`,
>   merged 2026-09-20, nine days after this node was written): an
>   append-only chain written in the fold's own transaction, with
>   point-in-time reconstruction ("the position this room held at
>   `before_ms`"). So "anything not restated is gone" is now true of the
>   position and false of the record behind it.
>
> The routing conclusion stands either way: a chain of positions is a
> history of what she *thought*, not an accumulator of what the room
> *said*, and `01 §4.2` needs the second. Standing is still not the home
> for accumulation.

## 3. Finding 2 — it is per-room, and stalling is per-thread

Standing is one paragraph for a whole conversation, rendered with its age:

```
- Friends (signal), as of 2 h ago: <one paragraph>
```

**It is the only input in the proactive prompt that carries a clock at all.**
So it is the pass's only evidence about movement — and it is the wrong shape
for the question. A room with five live threads where one moved yesterday
renders as a position 1 day old, and the four that have not moved in a
fortnight are invisible behind it.

`04 §2` fixes this properly by putting the clock on the thread, where the
judgement is. Standing keeps being what it is: a position, aged, not a ledger.

## 4. Finding 3 — the fire path never writes it

**The sharpest one, and it is documented in the code that causes it**
(`nora/app/fire.py`):

> This path READS the position and never writes one. That asymmetry is not a
> choice: S1 (fire turns debrief too) is blocked on the fire path retaining a
> `TagTrace` at all — `speak` discards the one `AgentRunner.invoke` hands back,
> a Phase 1 deferral flagged in that method — and there is nothing to fold
> without it.

The fire path is every unprompted occasion: reminder, gathering, **participation
and proactive**. Only a tag turn runs the debrief.

**The consequence is backwards.** The more a room talks without tagging her —
and the more correctly she participates or stays silent — the older her position
in that room becomes. The proactive pass then reads that stale position as its
only clock. **The room least likely to update her Standing is exactly the room
whose threads are stalling**, which is the room this bundle exists for.

Worse, it is self-reinforcing: a stale Standing makes the change-detection in
`_changed_since` less likely to fire, so the pass runs less often on the room
that needs it most.

**Out of scope to fix here** — it is a Phase 1 deferral in the fire path, owned
by whoever owns the debrief, and it needs `speak` to retain a `TagTrace`. Named
here because:

1. it bounds what `04 §2` can achieve — the note clock fixes the *thread*
   signal, and the *position* stays stale;
2. any later reading of "her position is out of date in the quiet rooms" has a
   cause, and it is not the model;
3. participation has been enabled on three rooms since 2026-09-10, so this
   asymmetry is live now, not hypothetical.

## 5. Open question — per-thread Standing

Not decided. Two readings:

- **Leave it.** Standing is a position and positions are about conversations.
  Per-thread state is what notes are for, and `04 §2` puts the clock there.
  This is the cheaper answer and probably the right one.
- **Split it.** If a room routinely runs several independent threads, one
  paragraph is a lossy summary of several positions, and the fold has to choose
  what to drop every turn — which is `26 §8`'s lossiness doing damage rather
  than doing its job.

Deciding it needs a reading nobody has: **how many independent live threads a
real room carries at once.** That is countable from the note store today, and
should be counted before the question is answered rather than argued from
intuition.
