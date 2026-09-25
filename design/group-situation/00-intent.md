---
type: design
title: "Intent: Nora reasons with the room's situation instead of waiting to be asked for it"
description: "A planning reply in the wedding room treated the district most of its members live in as a detour. The members' usual places were already derived and one tool call away; the turn's input carried a place for the caller only. This node records what the turn saw, replays recorded planning turns with the room's usual places rendered into the input, and proposes the block."
tags: [nora, personalisation, presence, context, tag-path, planning, measurement]
timestamp: 2026-09-25
status: measured-null
edges:
  - {dst: ../context-assembly/00-overview.md, rel: extends, provenance: asserted}
  - {dst: ../proposed/27-presence-analytics.md, rel: consumes, provenance: asserted}
  - {dst: ../dreamer-agent-loop/39-proactive-ooda-loop.md, rel: precedes, provenance: asserted}
---

# Intent

## 0. What the operator asked for

Operator, 2026-09-25: «I expect Nora to think with/for us, and not for us to
have to ask Nora to think.» Personalisation today is «you like hotpot, here is
hotpot nearby». What he wants is a reply that uses what Nora already holds:
where the people in the room live, where they will be at the time being
planned, and what else that day holds.

This bundle is step 1 of three:

1. **This node.** The tag turn carries the room's situation (§3).
2. Place identity (`design/proposed/27` §3.7), then the time-anchored forecast
   (Tier 4) with an instrument somebody other than its author can re-run.
3. A proactive pass that wakes on time anchors and world changes, not only on
   note and Standing changes (`nora/language/proactive.py::_changed_since`).

## 1. What the failing turn saw

Langfuse trace `aed7946b`, 2026-09-25 18:55 HKT, the wedding room (Signal, unit
`wedding`, `location = true`). A member asked for more hotpot choices in Tsim
Sha Tsui. The reply listed them and said a TST branch meant «no need to go into
Yuen Long», framing Yuen Long as the trip.

| | in the input? |
|---|---|
| the caller's peer card, which names his home area | yes |
| a card, or any place, for the other four members | **no** |
| each member's derived home and work place (`presence_derived_places`) | **no**. 4 of 5 members have a derived home; 1 has no presence data |
| what the group does that afternoon | **no**. It was said in the `friends` Signal room on 09-22, a different unit (DEC-105 split), so no block of this unit can carry it |
| a member's own worry that Yuen Long runs late | yes, in `<standing>` |

Tool calls on that turn: `place_info` ×7, `web_search` ×1. No `where_been`,
`saved_places` or `where_is`. In the turn before it, the reply asked one member
whether he was coming from Kai Tak or Tseung Kwan O; his derived home is in
Yuen Long District.

So the judgement turned on *where*, and the input carried *where* for one person
of five. The CMS rule for this is to read the input before rewriting the
instruction. The instruction is not the defect here: the facts were one tool
call away, and she calls tools to answer the literal question, not to check a
premise nobody raised.

## 2. What already exists

- **The derived layer.** `presence_derived_places` holds `home` / `work` per
  subject with a confidence. On 2026-09-25: 13 rows, 9 subjects,
  `saved_place_id` empty on all 13. `where_been what=places` serves it to the
  model on request, under R-28 (no coordinates, no corridor sequences).
- **The tag path's people blocks.** `<peer_card>` and `<peer_history>` are
  keyed on the tagger. `<people_in_window>` (issue #210) renders conclusions per
  speaker, but only on the participation turn, which has no tagger.
- **The disclosure grant.** `location = true` per room (DEC-74, DEC-79) is the
  operator's decision that the room's audience may hear where its members are.
  Every room in this measurement carries it.

## 3. The proposal: `<people_places>` on the tag turn

One block, per room, rendered only where the room has `location = true`: each
roster member's usual places, at **area** level (district or neighbourhood,
never a road or address), with low confidence marked and a member with no
pattern shown as having none. It says what it is: a pattern over past weeks,
not where anyone is now, and not something anyone in the room said.

Why area and not the substrate label: the label is a road name or a full street
address (one member's home label is his building's address). An area is what a
planning judgement needs, and it keeps a street address out of a prompt whose
output lands in a group chat. §4 measures both.

It is a `room`-keyed row in `prefill._BLOCKS`, in the per-turn message and
never the cached head, beside `people_in_window`.

What it does not do: carry live location (that is `where_is`, a different
freshness contract), carry anything across units, or forecast.

## 4. Measurement: replay of recorded planning turns

Instrument: the Langfuse replay harness recipe. For each case, take the
generation that wrote the recorded reply (so every tool result it had is still
in context), inject the block into the per-turn message, and ask the deployed
`nora` alias for the reply, forced to `send_reply`, temperature 0.7.

- **Cases.** Every `engagement` trace from 08-28 to 09-25 (484) filtered on
  planning language in the message or the last six transcript lines (84), then
  read by hand to keep the 14 where a place, time or meeting point was being
  decided: 10 in `friends` (Signal), 4 in `wedding`.
- **Arms.** A as recorded, B the block at area level, C the block with the
  substrate's raw labels. The harness proves A has no block and B and C have
  exactly one, before any model call.
- **Grading.** A blind judge (`nora-judge`, temperature 0), given the members'
  true home areas, the recent chat and the reply, never the arm. Criteria fixed
  before any result was read: `geo_use` (weighs the plan by where members live,
  start or go home), `geo_error` (a convenience claim the home areas
  contradict), `address_leak` (a member's street or address), `answers` (still
  answers what was asked), `geo_noise` (brings up homes where it does not help).
- **A positive result** is `geo_use` up and `geo_error` down in B against A,
  with `address_leak` at 0 and `answers` not lower.

### 4.1 Result, 2026-09-25: null

252 replays (14 cases, 3 arms, 6 samples). Excluded as ERROR, not as PASS: one
wedding case returned an empty reply on 17 of its 18 calls in every arm (a
harness failure for that case), and 10 other empty replies (A 7, B 2, C 1).
224 graded, the judge answered all 224.

| arm | n | geo_use | geo_error | address_leak | answers | geo_noise |
|---|---|---|---|---|---|---|
| A as recorded | 71 | 28 (0.39) | 2 | 0 | 71 | 0 |
| B area block | 76 | 31 (0.41) | 0 | 0 | 75 | 1 |
| C raw-label block | 77 | 33 (0.43) | 0 | 0 | 77 | 0 |

- **The block does not move the aggregate.** 0.39 against 0.41 at n of about
  75 per arm is noise. Granularity (C against B) moves nothing either, and no
  street address leaked in either arm.
- **It moves one case clearly.** A branch-choice turn in `friends` went from
  3/5 to 6/6 in B, with replies that set the branches against where the members
  live. Nine of the other twelve cases are within one sample.
- **The failing turn is not fixed.** On trace `aed7946b`, geo_use is A 3/6, B
  1/6, C 3/6, and B still writes «no need to go into Yuen Long».
- **The instrument is blind to the operator's defect.** The judge scored that
  sentence `geo_error = false` in every arm. Given only the home areas, it reads
  «no need to go into Yuen Long» as a remark about the restaurant brand's other
  branch, not a claim about the group. So `geo_error` cannot see the failure this
  node started from, and its 2 → 0 is not evidence of anything.

The instrument is not committed: its inputs are private rows (members' names,
home areas and chat), so it lives on the deploy host outside the checkout
(`~/workspace/nora-replay/group-situation-2026-09-25/`). By the rule
`design/proposed/27` §4.1 applies to its own forecast numbers, these are one
party's single measurement.

### 4.2 What the null says

Missing homes are not the main reason the replies stay shallow. With the
facts in front of her, she still answers the literal question («more hotpot in
TST») and does not reopen the premise behind it. The one reply that got the
failing turn right («TST is on the way: collect the suits at Harbour City,
shoes and shirts, then eat; Yuen Long is the reverse») came from the
**afternoon plan** in the transcript, not from anyone's home. What made TST
right is where the group *will be* at the event time and where it goes after,
which is step 2's forecast and the cross-unit plan (§5), not a static list of
homes.

**Decision: do not build `<people_places>` as measured.** Reopens if a
situation block that carries the event-time picture (who is where before, at
and after the anchor) is measured on these cases with a judge that can see the
premise failure.

## 5. Open questions

- **Cross-unit context.** The afternoon plan that made TST the right answer was
  said in another unit's room. DEC-105 splits notes and Standing by unit on
  purpose; Honcho is not split. Whether the wedding turn may read a sibling
  unit's Standing is a separate decision, not taken here.
- **Area resolution.** The derived label is a reverse-geocoded road. Area needs
  a district lookup from coordinates (Hong Kong's 18 district boundaries are
  open data) or the place's own sublocality. Not chosen yet.
- **Peer cards for the whole roster.** This node renders places only. Hobbies
  and habits for every member (not only the caller) are the same shape and a
  separate arm.
