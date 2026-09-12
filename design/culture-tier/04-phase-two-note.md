# Phase 2 — a note, not a plan

**This document is deliberately not executable.** It records what Phase 2 is,
why it cannot be specified yet, and what Phase 1 must produce before anyone
tries. Do not implement from it.

## 1. What Phase 2 is

Phase 1 is participation as *answering*: there is something she can supply, and
she supplies it.

Phase 2 is participation as *being in the circle*. The operator's example, from
the room:

> 「六七小隊已經升級做六七集團」
> 「我哋而家有埋秘書添」

Nobody names her. If the room's culture has been accumulating, she should
work out that she is the 秘書 — and that the right move may be to play along,
not to answer anything.

> 慢慢昇華 Nora，由一個 AI assistant 變成一個有 cognition 嘅 AI agent。

## 2. It is not proactive

Proactive means actively pushing the room forward — a reminder, a note coming
due. It exists, it is workspace-keyed, it fires into the home room.

Phase 2 extends **participation**, not proactive. Three things, not two:

| | |
|---|---|
| Participation | she supplies something she holds |
| Proactive | she raises something that is due |
| Phase 2 | she is a member and can take a role the room gives her |

Borrowing the proactive name would blur a boundary the operator has drawn
repeatedly. Phase 2 has no name yet; `ambient` is the least-bad candidate.

## 3. The capability is not blocked by intelligence

The gate cannot do this and never will: `reasoning_effort = "none"`,
`max_tokens = 512`. Realising the room has handed you a role is inference.

But it does not have to. The split:

| | gate | participation turn |
|---|---|---|
| budget | none, 512 tokens | full model, full doctrine |
| sees | transcript + standing (+ culture) | notes, `self_card`, memory, tools |
| answers | is this window worth her attention? | who am I here, and what now? |

`prefill.py` already builds a `<self_card>` block, and per #208 the
participation turn runs through the same `prefill.py`. **The material for the
秘書 realisation is already wired.**

What is missing is admission. The participation turn has run **zero times**, so
none of its context has ever been observed in production. Phase 2 is not
blocked by her not being clever enough; it is blocked by her never being let
in. Fix Phase 1 and the question becomes reachable for the first time.

## 4. Why this cannot be specified yet

**Measurement collapses at this altitude.** Every instrument in
`02-measurement` assumes there is something she could *supply*. Open-ended
participation has no supply — a friend riffing about a secretary is not asking
for anything, and there is no correct answer to score against.

The only grounded signal left is **uptake**: she said something, did anyone
engage. It is slow, noisy, and confounded by everything.

This is recorded now, before anyone builds, because the alternative is to
invent a precise-looking measurement for something unmeasurable and discover in
three months that it was 虛偽 — which is the exact failure this bundle exists
to correct one layer down. A single weak signal, named as weak, is the honest
position.

**And a room's culture cannot be back-tested.** Phase 1's `HOLDING` and
`override` can be computed retroactively from data already in the database.
Uptake against a culture that does not exist yet cannot. Phase 2 has no
retrospective baseline available to it at all.

## 5. What Phase 1 must produce first

1. The gate admits at a non-zero rate, and the rate is attributable per room
   (DEC-186).
2. Room culture has accumulated real revisions, with the lore sections written
   from day one (`01-the-tier §8`) so `shifted` has a history rather than a
   cold start.
3. `standing_in_it` has enough `decision_log` support to be more than a
   sentence about nothing — which needs `participation` to have a non-zero row
   count, which needs (1).
4. An uptake measure exists and has run long enough to have a base rate on the
   *tag* path, where she already speaks, so Phase 2's uptake has something to
   be compared against.

Item 4 is the one most likely to be skipped and is the one that makes the rest
interpretable.

## 6. Open, and deliberately unanswered

- What does `silence.md` become when silence is no longer the near-universal
  correct answer? It currently says most unasked-for turns end in silence, and
  under Phase 2 that stops being true — but by how much is a measurement, not
  an opinion.
- Does taking a role the room assigned require an operator grant, the way
  pushing does? The prior position was that pushing is the operator's call.
- What stops a room's culture from being poisoned by a member who talks about
  her constantly? `support_count` and the revision chain make it visible; they
  do not make it safe.
