---
type: design
title: "Verification: replay against what the group actually chose, then live acceptance"
description: "Offline replay of past decisions with only the evidence before each one (hit-at-k, NDCG), per-feature ablation, the live acceptance and ignore rates under the cap, and the negatives: no disclosure, no default for an unknown, no proposal over the cap, no ranking from uncited summaries."
tags: [nora, recommendations, verification, replay, ndcg, acceptance, negatives]
timestamp: '2026-10-25'
edges:
  - {dst: 20-ranker, rel: measures, provenance: asserted}
---

# 50 · Verification

## 1. Replay: would she have proposed what they chose?

The grounded measure, not a proxy. Take past decisions the unit actually made:
an outing at a venue (`rollup_outing`), a game they started playing together
(play sessions), an event's venue once set. For each, freeze the frame strictly before the choice using only what Nora
had recorded by that cutoff. Attendees, trigger, constraints and origins must
be known then, not inferred from the later outing or choice. Keep the actual
choice and its outcome in a separate label, unavailable to the ranker and
candidate generator; record where that label lands afterwards.

- **hit@3**: the actual choice is in the shortlist.
- **NDCG@10**: how high it ranked among all candidates.
- **Comparison arms:** a popularity baseline (the unit's most frequent place or
  genre), similarity alone, and the full score. The full score has to beat both
  baselines on held-out decisions for the recommendation type to ship.
- **Ablation:** drop one feature at a time; a feature that does not move the
  number is removed or re-weighted, not kept on faith.

Replay leaks if it sees anything Nora did not hold at the moment of the
choice. Cutting evidence by when it happened is not enough: a post back-filled
later has an old `taken_at`, and a social item's perceptions and counts change
after it is first recorded. So replay is **bitemporal**, the contract
[cognition-loop 10](../cognition-loop/10-tiers.md) §8 already states for
reading a tier as of a time:

- only rows **recorded** by the cutoff (`recorded_at` or the source's system-time equivalent, not `taken_at` or `at`), with the revision of each that was current at the cutoff;
- the cutoff is the instant before the choice, so the choice itself and every
  outcome after it are excluded;
- an input whose ordinary reader returns only current state (the social
  stream's item reader returns the latest location, perceptions and counts) is
  read through an as-of read or left out of the replay arm, never read as it
  is now;
- valid/event time answers when a claim applied; recorded/system time answers
  whether Nora held it. Require **both** applicability at frame time and
  availability before the cutoff, including the historical revision of each
  profile, reading, Rollup build, place identity and candidate description;
- derive facets from those frozen descriptions only. A current profile,
  later perception, backfilled post, edited source, reconciled place or
  current catalogue is not substituted for missing history;
- freeze eligible profile lines, their cited observations, frame, catalogue
  revisions and scoring parameters before the run. If historical inputs
  cannot be reconstructed, mark the case/arm unavailable with its missing
  inputs; exclude it from paired comparisons and report coverage. Do not
  label an incomplete arm a pass. Current deletion/disclosure gates still
  apply; historical access never restores permission.

**Repo evidence.** `social_item` distinguishes `taken_at`, `seen_at` and
`recorded_at` (`nora/infra/sql/schema.py:4689`,
`nora/infra/sql/schema.py:4697`, `nora/infra/sql/schema.py:4700`). Yet
`items_of` filters by posting time and joins current place, current media
readings and latest counts (`nora/infra/sql/social_stream.py:893`,
`nora/infra/sql/social_stream.py:912`, `nora/infra/sql/social_stream.py:925`,
`nora/infra/sql/social_stream.py:933`). That ordinary reader is not an as-of
reader. Step 3 must add historical reads or declare the arm unavailable.
Profile rows already carry write time and supersession
(`nora/domain/persona.py:311`, `nora/domain/persona.py:321`), but selecting
historical heads and cited-source revisions is also step-3 work.

## 2. Live

- **Acceptance rate** (picked or engaged) and **ignore rate** per recommendation type, from
  settled linked recommendation sends, reported weekly. Report open, unknown
  and unanswered separately and exclude them from acceptance/ignore denominators.
- **Unsolicited proposals per unit per week**, against the cap.
- A falling acceptance rate with a stable ignore rate means the shortlists are
  wrong; a rising ignore rate means she is proposing too often. They are read
  separately.

## 3. The negatives: what must NOT happen

- **No disclosure through the ranking.** Evidence whose requirements fail the
  destination room's gates (lineage, exclusions, location) is never read by the
  run. Hold permitted inputs fixed, vary only withheld evidence, and require
  identical triggers, candidate queries, scores, ordering and rendered items.
  Repeat with restricted locations and excluded profile lines. Every used or
  cited input's requirements propagate to the decision, following
  `design/standing-ledger/20-the-ledger.md:127` and
  `design/standing-ledger/30-turn-records.md:113`.
- **No default for an unknown.** A frame with a missing origin or schedule
  produces a proposal that names the assumption, or no proposal; never a
  silent home-origin ranking. Tested with a case whose answer flips when the
  missing schedule is known.
- **No proposal over the cap**, whatever learning from picks samples.
- **No veto ignored.** A candidate that fails a vetoable facet for an attendee
  with confident evidence never appears.
- **No competing likes store or self-confirmation.** Likes reads eligible
  profile revisions and their cited original evidence. Ranker output never
  feeds back as evidence of a member's taste.
- **No misattributed learning.** A synthetic group compromise changes no
  attendee's likes; an explicit personal liking changes evidence only for its
  author. `silent` changes the proposal policy only; `open`, `unknown` and
  `unanswered` change neither likes nor policy. A `tell` without a named person
  never becomes a negative just because nobody answered.
- **No undefined feature.** Exercise games coverage at 0.75, 1 and unknown
  ([20](20-ranker.md) §5), including missing cross-play data; a missing input
  must not become compatibility or a default score.
- **No future evidence.** Add a post with old event time but a later recorded
  time, a later profile revision and later item counts: none changes a frozen
  pre-choice ranking. The held-out choice and outcome are never ranker inputs.
- **No laundering across units.** A preference learned in one unit's rooms is
  never used to rank for another unit.
