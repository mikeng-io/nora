---
type: design
title: "Study: Standing folds drop open, dated plans, at least 10 in 206"
description: "Read-only study of 206 consecutive Standing revisions in production. A fold is not supposed to drop a plan still open and still ahead of it; at least 10 did, 5 of them substantive, clustered in one room on one day. The same afternoon plan was lost twice, in two rooms, three days apart. The 1200-character budget is not the cause. Most folds whose only new input was the turn's own steps still rewrote the paragraph almost entirely."
tags: [nora, standing, fold, plans, retention, measurement]
timestamp: 2026-09-25
status: measured
edges:
  - {dst: 00-intent.md, rel: follows, provenance: asserted}
  - {dst: ../standing-compact/20-verification.md, rel: measures, provenance: asserted}
  - {dst: ../dreamer-agent-loop/26-standing.md, rel: measures, provenance: asserted}
---

# Study: Standing drops open plans

> 2026-10-03 (DEC-277): retired; the Cognition session does this now. The note-candidates, curator, note-merge and proactive DECIDE passes, the ingest Standing event/rollup fold and the shadow machinery named here are deleted. This document is the record of what was decided at the time.


## 0. Why

In `00-intent.md` §4.2, the one replayed reply that reasoned about the wedding
dinner the way the operator did had the day's afternoon plan in its
`<standing>`. The failing turn's `<standing>` no longer did: the fold three
minutes earlier had dropped it. This node asks how often that happens.

The rule it measures is already written down. The fold objective
(`nora/dreamer/objectives/standing-debrief.md`) says «what is still undecided»
belongs, and «a plan still open keeps its date». The standing-compact eval's
`carry` kind (`design/standing-compact/20-verification.md`) tests «a thread the
prior held, which this turn did not touch, is still held». This study checks
that rule against production folds instead of the corpus.

## 1. Method

- **Population.** Every pair of consecutive `standing_revision` rows per room
  on norty: 214 revisions, 206 pairs, 8 rooms, 2026-09-20 22:13 to 09-25
  20:56 HKT (about five days; 104 of the pairs are from 09-22). Each pair
  also carries the messages between the two watermarks (`last_folded_row_id`)
  and the unit's notes that were open at the second write.
- **The criterion, fixed before any result was read.** A plan counts as
  DROPPED when all three hold:
  - PRIOR anchors it at or after NEW's write time;
  - NEW does not carry it, not even paraphrased or rewritten with its date;
  - the messages between did not settle, cancel, move or replace it.
- **Judge.** `auto/claude-sonnet` at temperature 0. It passed a positive
  control first: on the known wedding drop (17:59 → 18:52 on 09-25) it flagged
  the afternoon meet-up on both draws. `nora-judge` timed out on that control
  (930 s), and another alias returned a cached copy of Sonnet's answer, so
  neither was used.
- **No self-attestation.** I read every flag by hand, and also 8 unflagged
  pairs drawn at random from three rooms.

## 2. Result

| | count |
|---|---|
| pairs judged | 206 (no ERROR) |
| pairs the judge flagged | 18 |
| confirmed by hand as real drops | **10** (5 substantive, 5 minor) |
| judge false positives | 8: settled by a message (3), not an upcoming plan (3), still present in NEW or a date NEW corrected (2) |
| misses in 8 random unflagged pairs, three rooms | 0 |

The 5 substantive drops, anonymised:

1. A hand-over of borrowed gear the same evening, the only concrete thing the
   position said it was tracking, plus two members' stated joins for the next
   day's session. `friends`, 09-22.
2. Tonight's session time and Nora's own «I'll join when I get there».
   `friends`, 09-22.
3. A member's itinerary for the 27th: an afternoon event in one district, then
   the evening in another. Dropped about ten minutes after it was written.
   `friends`, 09-22.
4. Nora's own open question about whether a relay she sent needed her to wait
   for a reply and report back that evening. `friends`, 09-22. The weakest of
   the five: its date is an implied «tonight», and it is her question rather
   than the room's plan. It counts as substantive because it was an open
   obligation of hers, and the fold is told to keep those.
5. The 27th's afternoon meet-up point and the agreed order of stops before
   dinner. `wedding`, 09-25. This is the drop behind `00-intent.md`'s failing
   turn.

**Drops 3 and 5 are the same plan,** lost independently in two rooms three
days apart: once about ten minutes after it was written, once after the meet-up
had been carried for two days. The first time, nothing in the room had touched
it. The second time, members were talking about buying shirts and shoes, one of
the stops in the plan, but not about the meet-up point, and the meet-up was
dropped as well.

**Rate: a lower bound, and clustered.** 10 of 206 is what the judge found and
a reader confirmed; it depends on the judge's recall. Zero misses in 8 random
unflagged pairs still allows a miss rate around 30% over the 188 unflagged
pairs. The drops cluster: 7 of the 10, and 4 of the 5 substantive ones, fall in
one room between 16:24 and 19:38 on 09-22, and 5 of the 8 rooms show none. So
this is not «one fold in twenty» everywhere. It is a failure that happens and
bunches up in busy stretches, observed over five days by one reader, not a
committed instrument (§4).

## 3. What the data rules in and out

- **Not the budget.** Revision bodies: median 343 characters, p90 574, max
  1032, against a 1200 cap. No fold in the window was near it.
- **What gets dropped is often what the room said.** Three of the five
  substantive drops (1, 3, 5) are plans members stated; the other two carry
  something of Nora's own (2, 4), so this is a lean, not a rule. The mechanism
  that fits is the objective's anti-recap test («could someone who read the
  transcript write this sentence too?»), which reads a member's stated plan as
  a recap. (Its «drop what the room SAID first» order applies only over the
  character limit, which no fold here reached, so it explains none of these.)
  The anti-recap reading is true only while the plan is in the transcript
  window. That window is 40 messages. On the failing wedding turn, 40 messages
  covered 56 minutes, and after that nothing else holds the plan.
- **Corrected 2026-09-26: the folds with no new rows were mostly a deleted
  path.** 8 of the 11 pairs below reset `fold_count` to 1: that is the
  rebuild-after-N-folds re-derivation, which 7fdb24b4 (2026-09-24) deleted,
  and drops 1 and 2 are among them. The paragraph below stands as a reading
  of the old fold, not of today's. On today's fold (the 79 pairs written
  after 7fdb24b4) the confirmed drops are one substantive (drop 5) and one
  minor. See `design/standing-compact/40-open-plans.md` §6 for what that does
  to the build.
- **Most folds with no new rows still rewrite nearly everything.** 11 pairs had
  no new message rows between them. The rows had already been folded, so the
  debrief folded the turn's own `<steps>` against an empty batch (the watermark
  read, `nora/engagement/stages/debrief.py`). Eight of the 11 rewrote it to a
  similarity of 0.06 to 0.20 and about half the length; three were near-copies
  (0.94 or more).
  Two of the five substantive drops (1 and 2) are in these 11. The objective's
  «RE-JUDGE the prior position — the whole paragraph, every turn» is doing
  what it says. The cost is that a plan nothing touched can still be lost to a
  re-voicing.
- **Notes do not reliably catch it.** Of the five drops, the unit had an open
  note covering one fully (1). Drop 5 was covered in part: a note said the ride
  was sorted, not the itinerary. Drops 2, 3 and 4 had no note.

## 4. Instrument

Private rows, so not committed. It sits on the deploy host outside the
checkout, at `~/workspace/nora-replay/standing-drops-2026-09-25/`, holding the
pair builder, the judge prompt, the judged output and the hand labels.
Re-running it re-derives the flags. The hand labels are one reader's.

## 5. Options, not chosen here

- **A dated plan becomes a structured field that code carries**, shaped like
  #541's working set: the fold writes an open plan as an item with its anchor,
  and code keeps the item until the anchor passes or a fold explicitly settles
  it. Expiry by date is then a property of the render, not of a re-voicing,
  the same way the working set's TTL is.
- **A same-day plan becomes a note** at the moment it is stated. The
  note-candidates pass is the producer, but it did not produce one for drops
  2 to 4.
- **An instruction change** (keep plans that were stated even when the
  transcript still shows them). This is the cheapest option and the weakest:
  the Relevance Gate history (CMS in the operator's global rules) is the
  record of what instruction rewrites judged one example at a time do.

Whichever is chosen, the five drops in §2 are the `carry` cases to add to the
standing corpus before the change, so the change is measured against the
defect it is for.
